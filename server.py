from http.server import HTTPServer, BaseHTTPRequestHandler
import struct
import json
import time
import queue
import threading
import mysql.connector
from mysql.connector import pooling

# ------------------------------------------------------------------
# CONFIGURACIÓN DE BASE DE DATOS (phpMyAdmin / MySQL)
# ------------------------------------------------------------------
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",  # Cambia esto si tu MySQL tiene contraseña
    "database": "telemetria_db",
    "port": 3306
}

# Pool de conexiones MySQL para alto rendimiento
try:
    db_pool = mysql.connector.pooling.MySQLConnectionPool(
        pool_name="telemetry_pool",
        pool_size=5,
        **DB_CONFIG
    )
except Exception as e:
    print(f"⚠️ [Advertencia BD] No se pudo inicializar el pool MySQL: {e}")

# ------------------------------------------------------------------
# COLAS DE MEMORIA (DESACOPLAMIENTO DE LA TUBERÍA)
# ------------------------------------------------------------------
queue_raw = queue.Queue(maxsize=50000)        # Hilo 1 -> Hilo 2
queue_validated = queue.Queue(maxsize=50000)  # Hilo 2 -> Hilo 3
queue_sql_batch = queue.Queue(maxsize=50000)  # Hilo 3 -> Hilo 4

# Memoria global para el mapa en tiempo real
vehiculos_activos = {}

# ------------------------------------------------------------------
# HILO 2: VALIDADOR DE INTEGRIDAD Y ENCABEZADOS
# ------------------------------------------------------------------
def worker_validador():
    print("🟢 [Thread-2] Validador de Integridad iniciado.")
    while True:
        item = queue_raw.get()
        if item is None: break

        headers, payload, timestamp = item

        # Validar tamaño estricto de 16 bytes
        if len(payload) == 16:
            device_id = headers.get('X-Device-ID')
            if not device_id:
                forwarded_ip = headers.get('X-Forwarded-For')
                device_id = f"Movil_{forwarded_ip if forwarded_ip else 'Unknown'}"

            queue_validated.put((device_id, payload, timestamp))
        else:
            print(f"⚠️ [Validador] Trama descartada por tamaño incorrecto: {len(payload)}B")

        queue_raw.task_done()

# ------------------------------------------------------------------
# HILO 3: FORMATEADOR Y DESEMPAQUETADOR BINARIO (UNPACKER)
# ------------------------------------------------------------------
def worker_formateador():
    print("🟢 [Thread-3] Formateador SQL iniciado.")
    while True:
        item = queue_validated.get()
        if item is None: break

        device_id, payload, timestamp = item

        try:
            # Desempaquetado Big-Endian de 16 Bytes: !ffBHbbBBB
            lat, lon, speed, seq, accel_x, accel_y, net_type, os_ver, battery = struct.unpack("!ffBHbbBBB", payload)
            accel_x_val = round(accel_x / 10.0, 1)
            accel_y_val = round(accel_y / 10.0, 1)

            # Actualizar estado en memoria para el Mapa Leaflet
            vehiculos_activos[device_id] = {
                "lat": lat,
                "lon": lon,
                "speed": speed,
                "seq": seq,
                "accel_x": accel_x_val,
                "accel_y": accel_y_val,
                "net_type": net_type,
                "os_ver": os_ver,
                "battery": battery,
                "timestamp": timestamp
            }

            # Tupla optimizada para inserción SQL
            registro_sql = (
                device_id, lat, lon, speed, seq,
                accel_x_val, accel_y_val, net_type, os_ver, battery
            )

            queue_sql_batch.put(registro_sql)

        except Exception as e:
            print(f"❌ [Formateador Error] No se pudo desempaquetar payload: {e}")

        queue_validated.task_done()

# ------------------------------------------------------------------
# HILO 4: ESCRITOR EN BASE DE DATOS (BULK INSERT)
# ------------------------------------------------------------------
def worker_persistencia_db():
    print("🟢 [Thread-4] Persistencia MySQL (Escritor Batch) iniciado.")
    
    INSERT_QUERY = """
        INSERT INTO lecturas_telemetria 
        (device_id, lat, lon, speed, seq, accel_x, accel_y, net_type, os_ver, battery)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    batch = []
    last_flush = time.time()

    while True:
        try:
            item = queue_sql_batch.get(timeout=0.2)
            if item is None: break
            batch.append(item)
            queue_sql_batch.task_done()
        except queue.Empty:
            pass

        # Volcado a MySQL si se acumulan 100 registros o pasan 0.5 segundos
        if len(batch) >= 100 or (len(batch) > 0 and (time.time() - last_flush) > 0.5):
            conn = None
            try:
                conn = db_pool.get_connection()
                cursor = conn.cursor()
                cursor.executemany(INSERT_QUERY, batch)
                conn.commit()
                cursor.close()
            except Exception as e:
                print(f"❌ [DB Error] Falló el bulk insert en MySQL: {e}")
            finally:
                if conn and conn.is_connected():
                    conn.close()
            
            batch.clear()
            last_flush = time.time()

# ------------------------------------------------------------------
# INTERFAZ WEB CON MAPA INTERACTIVO (LEAFLET.JS)
# ------------------------------------------------------------------
MAPA_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard de Telemetría en Tiempo Real</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body { margin: 0; padding: 0; font-family: system-ui, sans-serif; background: #121212; color: #fff; }
        #map { height: 100vh; width: 100vw; }
        .panel { 
            position: absolute; top: 15px; right: 15px; z-index: 1000; 
            background: rgba(20, 20, 20, 0.9); color: #fff; padding: 15px; 
            border-radius: 8px; font-size: 13px; max-width: 320px; 
            box-shadow: 0 4px 12px rgba(0,0,0,0.5); border: 1px solid #333;
        }
        .nodo-item { background: #222; margin: 6px 0; padding: 8px; border-radius: 4px; border-left: 4px solid #2e7d32; }
    </style>
</head>
<body>
    <div class="panel">
        <h3 style="margin-top:0;">🛰️ Dispositivos en Campo</h3>
        <p>Nodos Activos: <b id="totalNodos" style="color: #4caf50;">0</b></p>
        <div id="listaNodos">Esperando datos de dispositivos...</div>
    </div>
    <div id="map"></div>

    <script>
        const map = L.map('map').setView([4.60971, -74.08175], 13);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap'
        }).addTo(map);

        const marcadores = {};
        const NOMBRES_RED = { 1: '2G', 2: '3G', 3: '4G', 4: 'WiFi' };

        async function actualizarMapa() {
            try {
                const res = await fetch('/api/vehiculos');
                const data = await res.json();
                
                const IDs = Object.keys(data);
                document.getElementById('totalNodos').innerText = IDs.length;
                let htmlLista = '';

                IDs.forEach(id => {
                    const v = data[id];
                    const redNombre = NOMBRES_RED[v.net_type] || 'Desconocida';
                    
                    const popupHTML = `
                        <div style="font-size: 12px; color: #000;">
                            <b style="font-size: 14px;">📱 ID: ${id}</b><br><hr>
                            📍 <b>Lat/Lon:</b> ${v.lat.toFixed(5)}, ${v.lon.toFixed(5)}<br>
                            🏎️ <b>Velocidad:</b> ${v.speed} km/h<br>
                            📦 <b>Paquete Seq:</b> #${v.seq}<br>
                            📐 <b>Accel (X/Y):</b> ${v.accel_x} / ${v.accel_y} m/s²<br>
                            📶 <b>Red:</b> ${redNombre} | 🤖 <b>Android:</b> v${v.os_ver}<br>
                            🔋 <b>Batería:</b> ${v.battery}%<br>
                            ⏱️ <b>Última señal:</b> hace ${v.hace_segundos}s
                        </div>
                    `;

                    htmlLista += `
                        <div class="nodo-item">
                            🟢 <b>${id}</b><br>
                            📍 ${v.lat.toFixed(4)}, ${v.lon.toFixed(4)} | 🏎️ ${v.speed} km/h<br>
                            📦 #${v.seq} | 🔋 ${v.battery}% | 📶 ${redNombre}
                        </div>
                    `;

                    if (marcadores[id]) {
                        marcadores[id].setLatLng([v.lat, v.lon]).getPopup().setContent(popupHTML);
                    } else {
                        marcadores[id] = L.marker([v.lat, v.lon]).addTo(map).bindPopup(popupHTML);
                    }
                });

                document.getElementById('listaNodos').innerHTML = htmlLista || 'Sin nodos activos';
            } catch(e) { console.error("Error al actualizar mapa:", e); }
        }

        setInterval(actualizarMapa, 1000);
    </script>
</body>
</html>
"""

# ------------------------------------------------------------------
# HILO 1: SERVIDOR HTTP / RECEPTOR DE TRAMAS
# ------------------------------------------------------------------
class TelemetryHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        payload = self.rfile.read(content_length)

        # Encolar inmediatamente el payload crudo sin bloquear la respuesta HTTP
        queue_raw.put((self.headers, payload, time.time()))

        # Responder de inmediato con 200 OK y CORS habilitado
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Device-ID')
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/vehiculos':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            ahora = time.time()
            respuesta = {}
            for k, v in vehiculos_activos.items():
                v_copy = v.copy()
                v_copy['hace_segundos'] = int(ahora - v['timestamp'])
                respuesta[k] = v_copy

            self.wfile.write(json.dumps(respuesta).encode('utf-8'))
        else:
            # Servir la interfaz web del Mapa Leaflet
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(MAPA_HTML.encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Device-ID')
        self.end_headers()

    def log_message(self, format, *args):
        return  # Silenciar logs HTTP predeterminados para mantener limpia la consola

def main(puerto=8080):
    # Arrancar hilos en segundo plano (Daemon Threads)
    threading.Thread(target=worker_validador, daemon=True).start()
    threading.Thread(target=worker_formateador, daemon=True).start()
    threading.Thread(target=worker_persistencia_db, daemon=True).start()

    server = HTTPServer(('0.0.0.0', puerto), TelemetryHandler)
    print(f"🚀 Servidor Multihilo de Telemetría escuchando en http://localhost:{puerto}")
    print(f"  ├─ Hilo 1: Servidor HTTP (Receptor)")
    print(f"  ├─ Hilo 2: Validador de Integridad")
    print(f"  ├─ Hilo 3: Formateador & Memoria en Vivo")
    print(f"  └─ Hilo 4: Persistencia Masiva MySQL (Bulk Insert)")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")

if __name__ == '__main__':
    main()

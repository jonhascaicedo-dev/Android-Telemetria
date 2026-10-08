from http.server import HTTPServer, BaseHTTPRequestHandler
import struct
import json
import time

# Memoria para los dispositivos activos en el mapa
vehiculos_activos = {}

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

class TelemetryHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        payload = self.rfile.read(content_length)

        # Habilitar CORS incluyendo la cabecera personalizada X-Device-ID
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Device-ID')
        self.end_headers()

        # Desempaquetado de 16 Bytes: !ffBHbbBBB
        if len(payload) == 16:
            lat, lon, speed, seq, accel_x, accel_y, net_type, os_ver, battery = struct.unpack("!ffBHbbBBB", payload)
            
            # Obtener ID único enviado por el cliente o respaldar por IP
            host_id = self.headers.get('X-Device-ID')
            if not host_id:
                forwarded_ip = self.headers.get('X-Forwarded-For')
                host_id = f"Movil_{forwarded_ip if forwarded_ip else self.client_address[0]}"

            # Guardar/Actualizar estado del vehículo
            vehiculos_activos[host_id] = {
                "lat": lat,
                "lon": lon,
                "speed": speed,
                "seq": seq,
                "accel_x": round(accel_x / 10.0, 1),
                "accel_y": round(accel_y / 10.0, 1),
                "net_type": net_type,
                "os_ver": os_ver,
                "battery": battery,
                "timestamp": time.time()
            }

            print(f"📦 [16B] {host_id} | Pos: ({lat:.5f}, {lon:.5f}) | Vel: {speed}km/h | Seq: #{seq} | Bat: {battery}%")

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
            # Servir la interfaz web del Mapa
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
        return

def main(puerto=8080):
    server = HTTPServer(('0.0.0.0', puerto), TelemetryHandler)
    print(f"🚀 Servidor escuchando en el puerto {puerto}")
    print(f"  ├─ Receptáculo POST: Recibiendo paquetes de 16B")
    print(f"  └─ Mapa Web en Vivo: http://localhost:{puerto}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")

if __name__ == '__main__':
    main()
CREATE DATABASE IF NOT EXISTS telemetria_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE telemetria_db;

CREATE TABLE IF NOT EXISTS lecturas_telemetria (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    device_id VARCHAR(50) NOT NULL,
    lat FLOAT(10,6) NOT NULL,
    lon FLOAT(10,6) NOT NULL,
    speed TINYINT UNSIGNED NOT NULL,
    seq SMALLINT UNSIGNED NOT NULL,
    accel_x FLOAT(4,1) NOT NULL,
    accel_y FLOAT(4,1) NOT NULL,
    net_type TINYINT UNSIGNED NOT NULL,
    os_ver TINYINT UNSIGNED NOT NULL,
    battery TINYINT UNSIGNED NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_device_time (device_id, created_at)
) ENGINE=InnoDB;
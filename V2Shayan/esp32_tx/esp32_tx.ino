/*
 * esp32_tx.ino — ESP32 Laser Transmitter (Sender Side)
 * ====================================================
 * Receives framed binary data from what.py over USB Serial,
 * modulates the CHT1230 laser diode on GPIO 4 using high-speed
 * On-Off Keying (OOK) bit-banging with ACK flow control.
 *
 * Pin Connections:
 *   - GPIO 4: Gate of N-Channel MOSFET (optionally via 100-220 ohm resistor)
 *   - GND: Common ground with MOSFET Source & Power Supply
 */

const int LASER_PIN = 4;

// Bit duration in microseconds. MUST match Receiver!
// 150 us ~= 6.6 Kbps (Target rate for SIH OISL link)
// 100 us = 10.0 Kbps
// 50 us  = 20.0 Kbps
const unsigned long BIT_DELAY_US = 150;

const uint8_t SYNC_1 = 0xAA;
const uint8_t SYNC_2 = 0x55;
const uint8_t ACK_BYTE = 0x06;

unsigned long lastHeartbeat = 0;

void setup() {
  Serial.begin(115200);
  pinMode(LASER_PIN, OUTPUT);
  digitalWrite(LASER_PIN, LOW); // Idle = Laser OFF
}

// Transmit 1 byte via OOK Laser modulation:
//   Idle:      LOW (Laser OFF)
//   Start Bit: HIGH (Laser ON)
//   Data Bits: 8 bits, MSB first (1 = HIGH, 0 = LOW)
//   Stop Bit:  LOW (Laser OFF)
void sendByteFast(uint8_t data) {
  // 1. Start Bit
  digitalWrite(LASER_PIN, HIGH);
  delayMicroseconds(BIT_DELAY_US);

  // 2. 8 Data Bits (MSB first)
  for (int i = 7; i >= 0; i--) {
    if ((data >> i) & 0x01) {
      digitalWrite(LASER_PIN, HIGH);
    } else {
      digitalWrite(LASER_PIN, LOW);
    }
    delayMicroseconds(BIT_DELAY_US);
  }

  // 3. Stop Bit (LOW) with 1.5-bit guard time
  digitalWrite(LASER_PIN, LOW);
  delayMicroseconds(BIT_DELAY_US + (BIT_DELAY_US / 2));
}

void loop() {
  // Read chunk from PC: [LEN] [PAYLOAD_BYTES...]
  if (Serial.available() > 0) {
    int len = Serial.read();
    if (len <= 0) return;

    uint8_t buffer[255];
    int bytesRead = Serial.readBytes(buffer, len);
    if (bytesRead != len) return;

    // Calculate XOR checksum across length + payload
    uint8_t checksum = len;
    for (int i = 0; i < len; i++) {
      checksum ^= buffer[i];
    }

    // Inter-packet idle gap
    digitalWrite(LASER_PIN, LOW);
    delayMicroseconds(BIT_DELAY_US * 4);

    // Transmit Laser Packet:
    // [SYNC1: 0xAA] [SYNC2: 0x55] [LEN] [PAYLOAD...] [CHECKSUM]
    sendByteFast(SYNC_1);
    sendByteFast(SYNC_2);
    sendByteFast((uint8_t)len);
    for (int i = 0; i < len; i++) {
      sendByteFast(buffer[i]);
    }
    sendByteFast(checksum);

    // Send ACK back to what.py
    Serial.write(ACK_BYTE);
    lastHeartbeat = millis();
  }

  // Optional Idle Heartbeat (send 0-length ping every 3s if no data)
  if (millis() - lastHeartbeat > 3000) {
    sendByteFast(SYNC_1);
    sendByteFast(SYNC_2);
    sendByteFast(0x00); // Len = 0 (Ping)
    sendByteFast(0x00); // Checksum = 0
    lastHeartbeat = millis();
  }
}

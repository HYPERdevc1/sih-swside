/*
 * esp32_tx.ino — ESP32 Dual-Laser Transmitter (Spatial Multiplexing)
 * ====================================================================
 */

const int LASER_1_PIN = 4;
const int LASER_2_PIN = 18;

// Bit duration (150 us per 2-bit slice = ~13.2 Kbps)
const unsigned long BIT_DELAY_US = 135;

const uint8_t SYNC_1 = 0xAA;
const uint8_t SYNC_2 = 0x55;
const uint8_t ACK_BYTE = 0x06;

unsigned long lastHeartbeat = 0;

void setup() {
  Serial.begin(115200);
  pinMode(LASER_1_PIN, OUTPUT);
  pinMode(LASER_2_PIN, OUTPUT);
  digitalWrite(LASER_1_PIN, LOW);
  digitalWrite(LASER_2_PIN, LOW);
}

void sendByteDualFast(uint8_t data) {
  // 1. Start Bit: Both lasers HIGH
  digitalWrite(LASER_1_PIN, HIGH);
  digitalWrite(LASER_2_PIN, HIGH);
  delayMicroseconds(BIT_DELAY_US);

  // 2. Data Bits: 4 slices of 2 bits (MSB pairs first)
  for (int i = 3; i >= 0; i--) {
    if ((data >> (i * 2 + 1)) & 0x01) {
      digitalWrite(LASER_1_PIN, HIGH);
    } else {
      digitalWrite(LASER_1_PIN, LOW);
    }
    
    if ((data >> (i * 2)) & 0x01) {
      digitalWrite(LASER_2_PIN, HIGH);
    } else {
      digitalWrite(LASER_2_PIN, LOW);
    }
    delayMicroseconds(BIT_DELAY_US);
  }

  // 3. Stop Bit: Both lasers LOW with 1.5-bit guard time
  digitalWrite(LASER_1_PIN, LOW);
  digitalWrite(LASER_2_PIN, LOW);
  delayMicroseconds(BIT_DELAY_US + (BIT_DELAY_US / 2)); 
}

void loop() {
  if (Serial.available() > 0) {
    int len = Serial.read();
    if (len <= 0) return;

    unsigned long startWait = millis();
    while(Serial.available() < len) {
        if(millis() - startWait > 2000) {
            // FIXED: If the PC stalled or you pressed Ctrl+C, flush the leftover 
            // bytes so they don't corrupt the next transmission.
            while(Serial.available()) Serial.read();
            return; 
        }
    }

    uint8_t buffer[255];
    int bytesRead = Serial.readBytes(buffer, len);
    if (bytesRead != len) return;

    uint8_t checksum = len;
    for (int i = 0; i < len; i++) {
      checksum ^= buffer[i];
    }

    // Force a 5 millisecond gap between chunks so the receiver can reset
    digitalWrite(LASER_1_PIN, LOW);
    digitalWrite(LASER_2_PIN, LOW);
    delay(5); 

    sendByteDualFast(SYNC_1);
    sendByteDualFast(SYNC_2);
    sendByteDualFast((uint8_t)len);
    for (int i = 0; i < len; i++) {
      sendByteDualFast(buffer[i]);
    }
    sendByteDualFast(checksum);

    Serial.write(ACK_BYTE);
    lastHeartbeat = millis();
  }

  if (millis() - lastHeartbeat > 3000) {
    sendByteDualFast(SYNC_1);
    sendByteDualFast(SYNC_2);
    sendByteDualFast(0x00); 
    sendByteDualFast(0x00); 
    lastHeartbeat = millis();
  }
}
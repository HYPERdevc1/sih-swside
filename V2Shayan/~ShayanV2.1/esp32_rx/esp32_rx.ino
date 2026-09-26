/*
 * esp32_rx.ino — ESP32 Dual-Laser Receiver
 * ====================================================================
 */

const int RX1_PIN = 16;
const int RX2_PIN = 17;

const unsigned long BIT_DELAY_US = 135;

enum State {
  WAIT_SYNC_1,
  WAIT_SYNC_2,
  READ_LENGTH,
  READ_PAYLOAD,
  READ_CHECKSUM
};

State rxState = WAIT_SYNC_1;

uint8_t packetLength = 0;
uint8_t payloadBuffer[256];
int payloadIndex = 0;
uint8_t runningChecksum = 0;
unsigned long lastByteTime = 0;

// Packet stats (for debugging)
unsigned long goodPackets = 0;
unsigned long badPackets = 0;

void setup() {
  Serial.begin(115200);
  pinMode(RX1_PIN, INPUT);
  pinMode(RX2_PIN, INPUT);
}

uint8_t sampleByteDual(unsigned long &timer) {
  uint8_t b = 0;
  for (int i = 3; i >= 0; i--) {
    while (micros() - timer < BIT_DELAY_US);
    timer += BIT_DELAY_US;
    
    if (digitalRead(RX1_PIN) == HIGH) b |= (1 << (i * 2 + 1));
    if (digitalRead(RX2_PIN) == HIGH) b |= (1 << (i * 2));
  }
  while (micros() - timer < BIT_DELAY_US);
  return b;
}

void loop() {
  // Feed the ESP32 background tasks so it never triggers a Watchdog reset
  yield();

  // Watchdog: reset state machine if no byte received within 15ms.
  // Must be ABOVE the TX's 5ms inter-packet gap but well below a full
  // packet duration (~60ms). 3ms was too aggressive for millis() granularity.
  if (rxState != WAIT_SYNC_1 && (millis() - lastByteTime > 15)) {
    rxState = WAIT_SYNC_1;
  }

  if (digitalRead(RX1_PIN) == HIGH || digitalRead(RX2_PIN) == HIGH) {
    unsigned long timer = micros();
    
    while (micros() - timer < (BIT_DELAY_US / 2));
    timer += (BIT_DELAY_US / 2);

    if (digitalRead(RX1_PIN) == HIGH || digitalRead(RX2_PIN) == HIGH) {
      uint8_t b = sampleByteDual(timer);
      lastByteTime = millis();

      switch (rxState) {
        case WAIT_SYNC_1:
          if (b == 0xAA) rxState = WAIT_SYNC_2;
          break;

        case WAIT_SYNC_2:
          if (b == 0x55) rxState = READ_LENGTH;
          else if (b == 0xAA) rxState = WAIT_SYNC_2; 
          else rxState = WAIT_SYNC_1;
          break;

        case READ_LENGTH:
          packetLength = b;
          if (packetLength > 0 && packetLength <= 250) {
            payloadIndex = 0;
            runningChecksum = packetLength;
            rxState = READ_PAYLOAD;
          } else {
            rxState = WAIT_SYNC_1;
          }
          break;

        case READ_PAYLOAD:
          payloadBuffer[payloadIndex++] = b;
          runningChecksum ^= b;
          if (payloadIndex >= packetLength) {
            rxState = READ_CHECKSUM;
          }
          break;

        case READ_CHECKSUM:
          if (b == runningChecksum) {
            // Packet valid — forward payload to rec.py over USB.
            // Do NOT call Serial.flush() here! It blocks the CPU for ~5.5ms
            // while the UART physically transmits, causing us to miss the
            // next laser packet's sync bytes. The UART hardware sends
            // asynchronously from the TX buffer.
            Serial.write(payloadBuffer, packetLength);
            goodPackets++;
          } else {
            badPackets++;
          }
          rxState = WAIT_SYNC_1;
          break;
      }
    }
  }
}
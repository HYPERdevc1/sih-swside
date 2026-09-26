/*
 * esp32_rx.ino — ESP32 Dual-Laser Receiver (Receiver Side)
 * =========================================================
 * Captures optical OOK pulses from TWO LM393 comparators:
 *   - RX1 on GPIO 16 (high bit of each 2-bit symbol)
 *   - RX2 on GPIO 17 (low bit of each 2-bit symbol)
 *
 * Each symbol period carries 2 bits in parallel, so a full byte
 * is sampled in 4 symbol clocks (2x throughput vs single laser).
 *
 * Verifies frame sync + XOR checksum before forwarding valid
 * payloads to rec.py over USB Serial.
 *
 * Pin Connections:
 *   - GPIO 16: LM393 #1 Output (Laser 1 / high bit)
 *   - GPIO 17: LM393 #2 Output (Laser 2 / low bit)
 *   - GND: Common ground with analog circuit
 */

const int RX1_PIN = 16;
const int RX2_PIN = 17;

// Bit (symbol) duration in microseconds. MUST match Transmitter!
// Each symbol carries 2 bits, so effective bit rate = 2 / BIT_DELAY_US
// 135 us => ~14.8 Kbps effective (2 bits per 135us symbol)
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

// Dual-laser byte sampler: reads 4 symbols × 2 bits = 8 bits (1 byte)
// RX1 = high bit of each pair, RX2 = low bit of each pair
// Bit layout: [RX1_3][RX2_3] [RX1_2][RX2_2] [RX1_1][RX2_1] [RX1_0][RX2_0]
//              bit7    bit6    bit5    bit4    bit3    bit2    bit1    bit0
uint8_t sampleByteDual(unsigned long &timer) {
  uint8_t b = 0;
  for (int i = 3; i >= 0; i--) {
    while (micros() - timer < BIT_DELAY_US);
    timer += BIT_DELAY_US;

    if (digitalRead(RX1_PIN) == HIGH) b |= (1 << (i * 2 + 1));
    if (digitalRead(RX2_PIN) == HIGH) b |= (1 << (i * 2));
  }
  // Wait through stop bit
  while (micros() - timer < BIT_DELAY_US);
  return b;
}

void loop() {
  // Feed the ESP32 background tasks so it never triggers a Watchdog reset
  yield();

  // Watchdog: If packet reception stalls > 200ms, reset state machine
  // (a 64-byte chunk at dual 135us/symbol takes ~35ms per byte,
  //  but inter-byte gaps can stretch, so 200ms is safe)
  if (rxState != WAIT_SYNC_1 && (millis() - lastByteTime > 200)) {
    rxState = WAIT_SYNC_1;
  }

  // Detect start condition: either laser going HIGH
  if (digitalRead(RX1_PIN) == HIGH || digitalRead(RX2_PIN) == HIGH) {
    unsigned long timer = micros();

    // Jump to center of start symbol for better sampling
    while (micros() - timer < (BIT_DELAY_US / 2));
    timer += (BIT_DELAY_US / 2);

    // Confirm it's a real start pulse (not noise glitch)
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
            // Packet valid! Forward payload to rec.py over USB
            Serial.write(payloadBuffer, packetLength);
            goodPackets++;
          } else {
            // Corrupted packet — drop it silently
            badPackets++;
          }
          rxState = WAIT_SYNC_1;
          break;
      }
    }
  }
}

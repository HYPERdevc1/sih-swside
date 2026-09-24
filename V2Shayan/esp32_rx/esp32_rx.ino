/*
 * esp32_rx.ino — ESP32 Laser Receiver (Receiver Side)
 * ====================================================
 * Captures optical OOK pulses from the LM393 comparator on GPIO 16,
 * samples incoming bytes using center-aligned bit sampling, verifies
 * frame synchronization and checksum, and pushes valid payloads to
 * rec.py over USB Serial.
 *
 * Pin Connections:
 *   - GPIO 16: LM393 Output (Pin 1) pulled up to 3.3V with 10k resistor
 *   - GND: Common ground with analog circuit
 */

const int RX_PIN = 16;

// MUST MATCH TRANSMITTER BIT_DELAY_US!
// 150 us ~= 6.6 Kbps (Target rate for SIH OISL link)
// 100 us = 10.0 Kbps
// 50 us  = 20.0 Kbps
const unsigned long BIT_DELAY_US = 150;

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

void setup() {
  Serial.begin(115200);
  pinMode(RX_PIN, INPUT);
}

// Bit-banged byte sampler with center-aligned timing
uint8_t sampleByte() {
  unsigned long timer = micros();

  // Jump to center of Start Bit
  while (micros() - timer < (BIT_DELAY_US / 2));
  timer += (BIT_DELAY_US / 2);

  // Read 8 data bits (MSB first)
  uint8_t b = 0;
  for (int i = 7; i >= 0; i--) {
    while (micros() - timer < BIT_DELAY_US);
    timer += BIT_DELAY_US;
    if (digitalRead(RX_PIN) == HIGH) {
      b |= (1 << i);
    }
  }

  // Wait until end of Stop Bit
  while (micros() - timer < BIT_DELAY_US);
  return b;
}

void loop() {
  // Watchdog: If packet reception stalls > 50ms, reset state machine
  if (rxState != WAIT_SYNC_1 && (millis() - lastByteTime > 50)) {
    rxState = WAIT_SYNC_1;
  }

  // Detect Start Bit (RISING edge / HIGH pulse)
  if (digitalRead(RX_PIN) == HIGH) {
    uint8_t b = sampleByte();
    lastByteTime = millis();

    switch (rxState) {
      case WAIT_SYNC_1:
        if (b == 0xAA) {
          rxState = WAIT_SYNC_2;
        }
        break;

      case WAIT_SYNC_2:
        if (b == 0x55) {
          rxState = READ_LENGTH;
        } else if (b == 0xAA) {
          rxState = WAIT_SYNC_2; // Stay in SYNC_2 if duplicate 0xAA preamble
        } else {
          rxState = WAIT_SYNC_1;
        }
        break;

      case READ_LENGTH:
        packetLength = b;
        if (packetLength > 0 && packetLength <= 250) {
          payloadIndex = 0;
          runningChecksum = packetLength;
          rxState = READ_PAYLOAD;
        } else {
          // If length is 0 (idle heartbeat ping), cleanly return to WAIT_SYNC_1
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
          // Packet valid and verified! Stream payload to rec.py over USB
          Serial.write(payloadBuffer, packetLength);
        }
        // Ready for next packet
        rxState = WAIT_SYNC_1;
        break;
    }
  }
}

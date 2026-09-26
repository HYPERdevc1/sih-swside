/*
 * esp32_rx.ino — ESP32 Dual-Laser Receiver (V2.1 debug build)
 * ====================================================================
 * Built-in LED (GPIO 2) feedback:
 *   - SOLID ON  = actively receiving a packet right now
 *   - BLINK     = packet received (quick flash on good, double-flash on bad CRC)
 *   - OFF       = idle, waiting for sync
 *
 * At startup, runs a 3-second alignment test that prints raw sampled
 * bytes to Serial so you can verify both laser channels work.
 */

const int RX1_PIN = 16;
const int RX2_PIN = 17;
const int LED_PIN = 2;   // ESP32 onboard LED

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

// Packet stats
unsigned long goodPackets = 0;
unsigned long badPackets = 0;

void setup() {
  Serial.begin(115200);
  pinMode(RX1_PIN, INPUT);
  pinMode(RX2_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // ── Startup alignment test (3 seconds) ──
  // Prints raw sampled bytes so you can check if BOTH channels work.
  // Open Serial Monitor at 115200 to see this. Close it before running rec.py.
  Serial.println();
  Serial.println("=== ALIGNMENT TEST (3s) ===");
  Serial.println("Expecting 0xAA sync bytes if laser is aligned.");
  Serial.println("If you see 0x00 or random values, adjust alignment.");
  Serial.println();

  unsigned long testEnd = millis() + 3000;
  int sampledCount = 0;

  while (millis() < testEnd) {
    yield();
    if (digitalRead(RX1_PIN) == HIGH || digitalRead(RX2_PIN) == HIGH) {
      unsigned long timer = micros();
      while (micros() - timer < (BIT_DELAY_US / 2));
      timer += (BIT_DELAY_US / 2);

      if (digitalRead(RX1_PIN) == HIGH || digitalRead(RX2_PIN) == HIGH) {
        uint8_t b = sampleByteDual(timer);
        // Show the raw byte, pin states, and whether it's a valid sync
        Serial.print("RX byte: 0x");
        if (b < 0x10) Serial.print("0");
        Serial.print(b, HEX);
        if (b == 0xAA) Serial.print(" << SYNC_1 OK");
        else if (b == 0x55) Serial.print(" << SYNC_2 OK");
        Serial.println();
        sampledCount++;

        // Blink LED on each detected byte
        digitalWrite(LED_PIN, HIGH);
        delayMicroseconds(500);
        digitalWrite(LED_PIN, LOW);
      }
    }
  }

  Serial.print("=== TEST DONE: sampled ");
  Serial.print(sampledCount);
  Serial.println(" bytes ===");
  if (sampledCount == 0) {
    Serial.println("WARNING: No laser pulses detected! Check:");
    Serial.println("  1. Is the laser physically on?");
    Serial.println("  2. Are GPIO 16/17 connected to LM393 outputs?");
    Serial.println("  3. Is the BPW34 aligned with the laser beam?");
  }
  Serial.println("Entering receive mode...\n");

  // Small delay so the alignment test text finishes printing
  // before rec.py starts reading
  delay(200);
  Serial.flush();
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
  yield();

  // Watchdog: reset state machine if no byte received within 15ms
  if (rxState != WAIT_SYNC_1 && (millis() - lastByteTime > 15)) {
    rxState = WAIT_SYNC_1;
    digitalWrite(LED_PIN, LOW);
  }

  if (digitalRead(RX1_PIN) == HIGH || digitalRead(RX2_PIN) == HIGH) {
    unsigned long timer = micros();

    while (micros() - timer < (BIT_DELAY_US / 2));
    timer += (BIT_DELAY_US / 2);

    if (digitalRead(RX1_PIN) == HIGH || digitalRead(RX2_PIN) == HIGH) {
      uint8_t b = sampleByteDual(timer);
      lastByteTime = millis();

      // LED ON while actively receiving
      if (rxState != WAIT_SYNC_1) {
        digitalWrite(LED_PIN, HIGH);
      }

      switch (rxState) {
        case WAIT_SYNC_1:
          if (b == 0xAA) {
            rxState = WAIT_SYNC_2;
            digitalWrite(LED_PIN, HIGH);  // LED on when sync detected
          }
          break;

        case WAIT_SYNC_2:
          if (b == 0x55) rxState = READ_LENGTH;
          else if (b == 0xAA) rxState = WAIT_SYNC_2;
          else {
            rxState = WAIT_SYNC_1;
            digitalWrite(LED_PIN, LOW);
          }
          break;

        case READ_LENGTH:
          packetLength = b;
          if (packetLength > 0 && packetLength <= 250) {
            payloadIndex = 0;
            runningChecksum = packetLength;
            rxState = READ_PAYLOAD;
          } else {
            rxState = WAIT_SYNC_1;
            digitalWrite(LED_PIN, LOW);
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
            // ✅ Good packet — forward to rec.py
            Serial.write(payloadBuffer, packetLength);
            goodPackets++;
            // Quick LED blink: ON-OFF
            digitalWrite(LED_PIN, HIGH);
            delayMicroseconds(200);
            digitalWrite(LED_PIN, LOW);
          } else {
            // ❌ Bad checksum — STILL forward the data so rec.py isn't deaf.
            // The image may have artifacts, but at least data flows and the
            // user can see SOMETHING. Better than total silence.
            Serial.write(payloadBuffer, packetLength);
            badPackets++;
            // Double-blink to signal bad CRC
            digitalWrite(LED_PIN, HIGH);
            delayMicroseconds(200);
            digitalWrite(LED_PIN, LOW);
            delayMicroseconds(200);
            digitalWrite(LED_PIN, HIGH);
            delayMicroseconds(200);
            digitalWrite(LED_PIN, LOW);
          }
          rxState = WAIT_SYNC_1;
          break;
      }
    }
  }
}
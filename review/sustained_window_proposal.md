# Proposal: Wakeup Detection with Sustained Window Filters

This proposal explores robust algorithms for detecting baby wakeups using the HLK-LD2410C mmWave radar OUT pin. It addresses the issues of under-detection (due to strict threshold filters) and over-detection (due to brief sleep movements).

---

## 1. The Core Engineering Challenge

The radar's digital output pin behaves as follows:
* `HIGH`: Motion detected.
* `LOW`: No motion detected.

A baby's wakeup sequence consists of active movement bursts (stretching, rolling, crying) interspersed with brief pauses (100ms–2s). 

### The Failure Mode of Strict Window Filtering
In the initial sketch, the algorithm required an uninterrupted `HIGH` state for `10,000ms`. 
If the radar signal drops to `LOW` for even a single loop cycle (`100ms`) due to:
* Signal jitter or respiration pauses,
* The baby shifting angles out of a gate zone,
* Micro-movement transitions,

the detection timer immediately resets to zero. In practice, this results in **zero alerts** during actual wakeups.

---

## 2. Proposed Algorithmic Solutions

We propose four candidate algorithms, sorted by implementation complexity and real-world robustness.

### Option A: Dropout-Tolerant Timer (Hysteresis)
This algorithm allows brief signal dropouts (e.g., up to 2 seconds) during the sustained window. The window is only reset if the signal remains `LOW` for longer than the maximum dropout limit.

```cpp
// --- CONFIGURATION ---
const unsigned long SUSTAINED_WINDOW = 10000;  // 10 seconds of active monitoring
const unsigned long MAX_DROPOUT_TIME = 2000;   // Allow up to 2 seconds of silence

// --- STATE VARIABLES ---
unsigned long radarDetectionStart = 0;
unsigned long lastActiveTime = 0;
bool isTracking = false;

void processRadar(bool currentRadarState) {
  unsigned long currentMillis = millis();

  if (currentRadarState) {
    if (!isTracking) {
      radarDetectionStart = currentMillis;
      isTracking = true;
      Serial.println("Motion started. Tracking...");
    }
    lastActiveTime = currentMillis; // Keep track of the last time we saw active motion
    
    // Check if we have met the sustained window
    if (currentMillis - radarDetectionStart >= SUSTAINED_WINDOW) {
      triggerAlarm();
      isTracking = false; // Reset tracker
    }
  } else {
    // If we are tracking, but it went low, check if we exceeded the dropout limit
    if (isTracking && (currentMillis - lastActiveTime > MAX_DROPOUT_TIME)) {
      Serial.println("False alarm discarded: Dropout threshold exceeded.");
      isTracking = false; // Reset tracker
    }
  }
}
```
* **Pros**: Simple to write and low memory footprint.
* **Cons**: Does not account for the *frequency* of motion, only the time elapsed since the first trigger.

---

### Option B: Leaky Bucket Accumulator
This approach models activity as a reservoir of water. Active motion pours water into the bucket, and periods of silence drain it. An alert triggers when the bucket overflows.

```cpp
// --- CONFIGURATION ---
const int BUCKET_MAX = 100;         // Upper limit of the bucket
const int TRIGGER_THRESHOLD = 80;   // Trigger alarm when bucket reaches this level
const int ADD_WEIGHT = 5;           // How fast the bucket fills (on HIGH)
const int SUB_WEIGHT = 2;           // How fast the bucket drains (on LOW)
const unsigned long SAMPLE_RATE = 100; // Sample every 100ms

// --- STATE VARIABLES ---
int bucketLevel = 0;
unsigned long lastSampleTime = 0;

void processRadar(bool currentRadarState) {
  unsigned long currentMillis = millis();
  if (currentMillis - lastSampleTime >= SAMPLE_RATE) {
    lastSampleTime = currentMillis;

    if (currentRadarState) {
      bucketLevel = min(BUCKET_MAX, bucketLevel + ADD_WEIGHT);
    } else {
      bucketLevel = max(0, bucketLevel - SUB_WEIGHT);
    }

    if (bucketLevel >= TRIGGER_THRESHOLD) {
      triggerAlarm();
      bucketLevel = 0; // Empty the bucket after triggering
    }
  }
}
```
* **Pros**: Highly configurable, support for asymmetrical rise and decay.
* **Cons**: Harder to map parameters directly to physical event durations.

---

### Option C: Sliding Window / Duty Cycle Filter
This approach maintains a circular buffer of the last $N$ readings (representing the last 15 seconds). It triggers an alarm if the duty cycle (ratio of `HIGH` to `LOW` samples) is above a configured percentage (e.g., 70%).

```cpp
// --- CONFIGURATION ---
const int WINDOW_SIZE = 150;      // 150 samples (15 seconds at 100ms sample rate)
const float TRIGGER_RATIO = 0.70; // Trigger alert if motion is present >70% of the window

// --- STATE VARIABLES ---
bool samples[WINDOW_SIZE] = {false};
int writeIndex = 0;
int activeCount = 0;
unsigned long lastSampleTime = 0;

void processRadar(bool currentRadarState) {
  unsigned long currentMillis = millis();
  if (currentMillis - lastSampleTime >= 100) {
    lastSampleTime = currentMillis;

    // Subtract the oldest sample from activeCount
    activeCount -= samples[writeIndex] ? 1 : 0;

    // Save the new sample and add it to activeCount
    samples[writeIndex] = currentRadarState;
    activeCount += currentRadarState ? 1 : 0;

    // Increment write index (circular buffer)
    writeIndex = (writeIndex + 1) % WINDOW_SIZE;

    float ratio = (float)activeCount / WINDOW_SIZE;
    if (ratio >= TRIGGER_RATIO) {
      triggerAlarm();
      memset(samples, 0, sizeof(samples)); // Clear history after alert
      activeCount = 0;
    }
  }
}
```
* **Pros**: Mathematically precise. Directly calculates activity density. If a baby just rolls over (moving for 2-3 seconds) and settles back down, the active duty cycle remains low (~15%) and does not trigger. It triggers only on sustained activity.
* **Cons**: Requires a RAM array buffer (150 bytes), which is negligible on ESP8266.

---

### Option D: Two-Point Checkpoint Sampling
This approach triggers a delay timer upon the first detection. After a configured period $T$ (e.g., 15 seconds), it samples the radar output again. If motion is still detected at that specific instant, it declares the baby is awake.

```cpp
// --- CONFIGURATION ---
const unsigned long CHECK_DELAY = 15000; // 15-second delay before checkpoint sample

// --- STATE VARIABLES ---
unsigned long firstTriggerTime = 0;
bool isWaitingForCheckpoint = false;

void processRadar(bool currentRadarState) {
  unsigned long currentMillis = millis();

  // Motion detected while idle starts the checkpoint timer
  if (currentRadarState && !isWaitingForCheckpoint) {
    firstTriggerTime = currentMillis;
    isWaitingForCheckpoint = true;
    Serial.println("Initial motion. Starting checkpoint timer...");
  }

  // Check the checkpoint
  if (isWaitingForCheckpoint && (currentMillis - firstTriggerTime >= CHECK_DELAY)) {
    if (currentRadarState) {
      triggerAlarm(); // Motion is STILL present
    } else {
      Serial.println("Baby settled down. Discarding trigger.");
    }
    isWaitingForCheckpoint = false; // Reset
  }
}
```
* **Pros**: Extremely low resource usage. Simple state machine with no buffers or counters.
* **Cons**: Highly susceptible to point-in-time synchronization errors (see analysis below).

---

## 3. Objective Comparison: Option B vs. Option C

While both Option B (Leaky Bucket) and Option C (Sliding Window) are designed to handle intermittent movements, they behave differently in terms of temporal memory, tuning knobs, and complexity.

### 1. Asymmetrical Weighting (Memory Decay)
* **Option B (Leaky Bucket)**:
  * Allows **asymmetric weighting** for rising activity vs. decaying activity.
  * For example, by setting `ADD_WEIGHT = 5` and `SUB_WEIGHT = 2`, the bucket fills quickly (fast trigger during high activity) but drains slowly. If the baby is active for 5 seconds and then stops, the bucket slowly drains over 12.5 seconds.
  * This allows the system to bridge wide, silent intervals without resetting, while still allowing the system to calm down if no further motion occurs.
* **Option C (Sliding Window)**:
  * Has **symmetric temporal memory**. Every sample remains in the window for exactly $T$ seconds (the window length, e.g., 15s) and is then discarded abruptly.
  * This can cause the **"Memory Hangover"** effect: if a baby moves intensely for 5 seconds at the beginning of the window, that activity contributes to the duty cycle for exactly 15 seconds. Even if the baby goes completely still, a tiny twitch 14 seconds later can push the duty cycle over the threshold and trigger the alarm.

### 2. Tuning Knobs and Intuitiveness
* **Option B**:
  * Has three abstract parameters (`ADD_WEIGHT`, `SUB_WEIGHT`, `TRIGGER_THRESHOLD`).
  * While powerful, tuning these values requires testing and does not map directly to time values. For instance, explaining what "a drain rate of 2 points per 100ms" means to a user is non-intuitive.
* **Option C**:
  * Has two highly intuitive physical parameters: `WINDOW_SIZE` (in seconds) and `TRIGGER_RATIO` (as a percentage of active time).
  * It is easy to understand: "If the baby is active for 70% of the last 15 seconds, alarm." This is much easier to configure and maintain.

### 3. Resource Efficiency
* **Option B**:
  * Requires $O(1)$ space and time. It only stores a single level counter.
* **Option C**:
  * Requires $O(N)$ space to store the buffer array and $O(1)$ time (when using a running sum circular buffer). 

---

## 4. Summary Matrix: Option C vs. Option D vs. Option B

| Feature | Option C: Sliding Window | Option D: Two-Point Checkpoint | Option B: Leaky Bucket |
| :--- | :--- | :--- | :--- |
| **Memory Cost** | Low (~150 bytes) | **Minimal (0 bytes)** | **Minimal (0 bytes)** |
| **CPU Cycles** | Low | **Minimal** | **Minimal** |
| **Symmetry** | Symmetric | Point-in-time | **Asymmetric (Configurable)** |
| **Intuitiveness** | **High** (Seconds & %) | High (Seconds delay) | Low (Abstract Weights) |
| **Rollover Immunity** | **High** | Medium | **High** |
| **Resilience to Pauses** | **High** | Low | **High** |

### Recommendation
If **precise control over rise and decay rates** is desired (e.g., you want the system to remember activity for a long time but trigger very quickly on high-intensity movement), **Option B (Leaky Bucket)** is superior.

If **ease of understanding, physical configuration matching, and symmetric historical constraints** are preferred, **Option C (Sliding Window)** is superior. Given the ESP8266's memory abundance, **Option C** is generally the more intuitive and robust baseline.

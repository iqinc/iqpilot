#pragma once

#include "iqdbc/safety/declarations.h"

// BYD Sealion 7 (e-Platform 3.0), Konik BYD-6 relay harness.
//   bus 0 = chassis CAN, bus 2 = camera / ADAS side.
#define BYD_STEER_MODULE_2      0x11FU  // RX from EPS,      steering angle
#define BYD_STEERING_TORQUE     0x1FCU  // RX from EPS,      driver torque + EPS state
#define BYD_WHEEL_SPEEDS        0x1F0U  // RX from ESP,      vehicle speed
#define BYD_DRIVE_STATE         0x242U  // RX from VCU,      gear + brake pressed
#define BYD_PEDAL               0x342U  // RX from VCU,      accelerator pedal
#define BYD_ACC_HUD_ADAS        0x32DU  // RX from ADAS(b0), cruise state
#define BYD_STEERING_MODULE_ADAS 0x1E2U // TX to  EPS,       angle command
#define BYD_LKAS_HUD_ADAS       0x316U  // TX to  cluster,   LKAS HUD
#define BYD_ACC_CMD             0x32EU  // TX to  IPB,       accel command
#define BYD_PCM_BUTTONS         0x3B0U  // TX,               cruise cancel

// WHEEL_SPEEDS scale, kph per LSB. PROVISIONAL - keep in lockstep with byd_sealion_7.dbc.
#define BYD_WHEEL_SPEED_SCALE 0.0725f

// STEERING_TORQUE.DRIVER_TORQUE counts (0.1 Nm/LSB). MUST equal CarControllerParams
// .STEER_DRIVER_OVERRIDE * 10 in values.py: carstate.py latches on the same instantaneous
// sample, and if the two sides disagree openpilot and the panda desync into controlsMismatch.
#define BYD_DRIVER_TORQUE_OVERRIDE 120

// A steering override disengages and stays disengaged until the driver deliberately re-arms,
// either by cycling stock cruise or pressing the LKAS/ICC button (0x3B0 bit 6, confirmed
// on-car). Mirrors the override latch in carstate.py.
static bool byd_override_latched = false;
static bool byd_lkas_btn_prev = false;

// ACC_CMD.ACCEL_CMD is an 8-bit field at 0.05 m/s^2 per LSB with a -5 m/s^2 offset, so raw 100
// is 0.0 m/s^2. Limits below are in offset-corrected LSBs.
#define BYD_ACCEL_OFFSET 100

static uint8_t byd_get_counter(const CANPacket_t *msg) {
  uint8_t cnt = 0U;
  if ((msg->addr == BYD_STEERING_TORQUE) || (msg->addr == BYD_WHEEL_SPEEDS) || (msg->addr == BYD_PEDAL)) {
    cnt = (msg->data[6] >> 4) & 0xFU;
  } else if (msg->addr == BYD_ACC_HUD_ADAS) {
    cnt = msg->data[6] & 0xFU;
  } else if (msg->addr == BYD_STEER_MODULE_2) {
    cnt = msg->data[4] & 0xFU;
  } else {
  }
  return cnt;
}

static uint32_t byd_get_checksum(const CANPacket_t *msg) {
  return msg->data[7];
}

static uint32_t byd_compute_checksum(const CANPacket_t *msg) {
  // Every 8-byte BYD frame: the last byte is the inverted sum of the first seven.
  uint8_t sum = 0U;
  for (int i = 0; i < 7; i++) {
    sum = (uint8_t)(sum + msg->data[i]);
  }
  return (uint32_t)((uint8_t)(~sum));
}

static void byd_rx_hook(const CANPacket_t *msg) {
  if (msg->bus == 0U) {
    // Steering angle: STEER_ANGLE_2, 0.1 deg/LSB, signed, little endian
    if (msg->addr == BYD_STEER_MODULE_2) {
      int angle_meas_new = to_signed((msg->data[1] << 8) | msg->data[0], 16);
      update_sample(&angle_meas, angle_meas_new);
    }

    // Vehicle speed. NOTE: on the Sealion 7 this address carries four 12-bit wheel speeds, not
    // the Atto 3's single 16-bit WHEELSPEED_CLEAN. Decoding it the Atto 3 way yields garbage,
    // and vehicle speed feeds the angle rate limits.
    //   FL 0|12, FR 16|12, RL 28|12, RR 40|12
    if (msg->addr == BYD_WHEEL_SPEEDS) {
      uint32_t fl = ((uint32_t)msg->data[0]) | ((uint32_t)(msg->data[1] & 0xFU) << 8);
      uint32_t fr = ((uint32_t)msg->data[2]) | ((uint32_t)(msg->data[3] & 0xFU) << 8);
      uint32_t rl = ((uint32_t)(msg->data[3] >> 4)) | ((uint32_t)msg->data[4] << 4);
      uint32_t rr = ((uint32_t)msg->data[5]) | ((uint32_t)(msg->data[6] & 0xFU) << 8);

      float speed = ((float)(fl + fr + rl + rr) / 4.0f) * BYD_WHEEL_SPEED_SCALE;
      vehicle_moving = speed > 0.0f;
      UPDATE_VEHICLE_SPEED(speed * KPH_TO_MS);
    }

    // Brake pressed. This MUST stay the same bit that carstate.py reads (DRIVE_STATE bit 37);
    // if the two latches read different sources a light brake graze clears only one of them and
    // controlsd raises "Controls Mismatch".
    if (msg->addr == BYD_DRIVE_STATE) {
      brake_pressed = ((msg->data[4] >> 5) & 0x1U) != 0U;
    }

    // Gas pressed, from the real accelerator pedal (GAS_PEDAL, 0.01/LSB). NOT
    // DRIVE_STATE.RAW_THROTTLE, which is powertrain torque demand and pulses on its own while
    // accelerating.
    if (msg->addr == BYD_PEDAL) {
      gas_pressed = msg->data[0] > 10U;
    }

    // Driver torque, and the override latch. DRIVER_TORQUE is 4|12 signed.
    if (msg->addr == BYD_STEERING_TORQUE) {
      int torque_driver_new = to_signed(((msg->data[1] & 0xFFU) << 4) | (msg->data[0] >> 4), 12);
      update_sample(&torque_driver, torque_driver_new);
      if (SAFETY_ABS(torque_driver_new) > BYD_DRIVER_TORQUE_OVERRIDE) {
        byd_override_latched = true;
      }
    }

    // LKAS/ICC button (0x3B0 bit 6) re-arms after an override
    if (msg->addr == BYD_PCM_BUTTONS) {
      bool lkas_btn = ((msg->data[0] >> 6) & 0x1U) != 0U;
      if (lkas_btn && !byd_lkas_btn_prev) {
        byd_override_latched = false;
      }
      byd_lkas_btn_prev = lkas_btn;
    }

    // Cruise state. The ADAS/ACC ECU is on the chassis bus, not behind the camera relay.
    // CRUISE_STATE is the high nibble of byte 5: 0=off, 1=available, 2=engaged,
    // 3=engaged and commanding accel. PR #3337/#3352 read ACC_STATE from byte 2, which is a
    // constant 0x3c here and can only ever report 7 (ERROR).
    if (msg->addr == BYD_ACC_HUD_ADAS) {
      uint8_t cruise_state = msg->data[5] >> 4;
      if (cruise_state < 2U) {
        byd_override_latched = false;
      }
      bool acc_on = (cruise_state >= 2U) && !byd_override_latched;
      pcm_cruise_check(acc_on);
    }
  }
}

static bool byd_tx_hook(const CANPacket_t *msg) {
  const AngleSteeringLimits BYD_STEERING_LIMITS = {
    .max_angle = 3900,  // 390 deg
    .angle_deg_to_can = 10,
    .frequency = 50U,
  };

  const AngleSteeringParams BYD_STEERING_PARAMS = {
    .slip_factor = -0.000572451189655154,  // calc_slip_factor(VM) for BYD_SEALION_7
    .steer_ratio = 16.0,
    .wheelbase = 2.93,
  };

  // ACCEL_CMD in offset-corrected LSBs of 0.05 m/s^2
  const LongitudinalLimits BYD_LONG_LIMITS = {
    .max_accel = 40,    //  2.0 m/s^2
    .min_accel = -70,   // -3.5 m/s^2
    .inactive_accel = 0,
    .zero_accel = 0,
  };

  bool tx = true;

  if (msg->bus == 0U) {
    // Steering angle command: STEER_ANGLE 24|16, 0.1 deg/LSB signed; STEER_REQ is bit 21
    if (msg->addr == BYD_STEERING_MODULE_ADAS) {
      int desired_angle = to_signed((msg->data[4] << 8) | msg->data[3], 16);
      bool steer_req = ((msg->data[2] >> 5) & 0x1U) != 0U;

      if (steer_angle_cmd_checks_vm(desired_angle, steer_req, BYD_STEERING_LIMITS, BYD_STEERING_PARAMS)) {
        tx = false;
      }
    }

    // Longitudinal command
    if (msg->addr == BYD_ACC_CMD) {
      int desired_accel = (int)msg->data[0] - BYD_ACCEL_OFFSET;
      if (longitudinal_accel_checks(desired_accel, BYD_LONG_LIMITS)) {
        tx = false;
      }
    }
  }

  return tx;
}

static safety_config byd_init(uint16_t param) {
  // 0x1E2 and 0x316 are transmitted continuously, gated only by STEER_REQ. check_relay blocks
  // the camera's own copies, so openpilot is the only source of both while installed. The EPS
  // latches a fault if the 0x1E2 stream stops while it is actuating.
  static const CanMsg BYD_TX_MSGS[] = {
    {BYD_STEERING_MODULE_ADAS, 0, 8, .check_relay = true},
    {BYD_LKAS_HUD_ADAS, 0, 8, .check_relay = true},
    {BYD_PCM_BUTTONS, 0, 8, .check_relay = false},
  };

  // Longitudinal is only offered on a gateway harness, where the ACC ECU is behind the relay
  // and 0x32E is genuinely filterable. On a camera harness the ACC ECU is in front of the
  // relay, so alphaLongitudinalAvailable is false there and this list is never selected -
  // check_relay would otherwise fire on every stock ACC frame.
  static const CanMsg BYD_LONG_TX_MSGS[] = {
    {BYD_STEERING_MODULE_ADAS, 0, 8, .check_relay = true},
    {BYD_LKAS_HUD_ADAS, 0, 8, .check_relay = true},
    {BYD_ACC_CMD, 0, 8, .check_relay = true},
    {BYD_PCM_BUTTONS, 0, 8, .check_relay = false},
  };

  // 4-bit rolling counters, so max_counter is 15. Leaving it 0 does not "skip" the check, it
  // pins wrong_counters at the failure threshold and every frame is rejected.
  static RxCheck byd_rx_checks[] = {
    {.msg = {{BYD_STEER_MODULE_2, 0, 5, 100U, .ignore_checksum = true, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  // steering angle (4-bit checksum, not the byte-7 one)
    {.msg = {{BYD_STEERING_TORQUE, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},                           // driver torque + EPS state
    {.msg = {{BYD_WHEEL_SPEEDS, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},                              // vehicle speed
    {.msg = {{BYD_DRIVE_STATE, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},  // gear + brake (no counter/checksum)
    {.msg = {{BYD_PEDAL, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},                                     // accelerator pedal
    {.msg = {{BYD_ACC_HUD_ADAS, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},                              // cruise state (chassis bus, not behind the relay)
  };

  byd_override_latched = false;
  byd_lkas_btn_prev = false;

  bool byd_longitudinal = false;

  SAFETY_UNUSED(param);
  #ifdef ALLOW_DEBUG
    const int FLAG_BYD_LONG_CONTROL = 1;
    byd_longitudinal = GET_FLAG(param, FLAG_BYD_LONG_CONTROL);
  #endif

  // cppcheck-suppress knownConditionTrueFalse
  return byd_longitudinal ? BUILD_SAFETY_CFG(byd_rx_checks, BYD_LONG_TX_MSGS) : \
                            BUILD_SAFETY_CFG(byd_rx_checks, BYD_TX_MSGS);
}

const safety_hooks byd_hooks = {
  .init = byd_init,
  .rx = byd_rx_hook,
  .tx = byd_tx_hook,
  .get_counter = byd_get_counter,
  .get_checksum = byd_get_checksum,
  .compute_checksum = byd_compute_checksum,
};

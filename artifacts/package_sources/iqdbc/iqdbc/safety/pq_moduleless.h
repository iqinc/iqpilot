/*
 * Copyright © IQ.Lvbs, a part of Project Teal Lvbs.
 * All Rights Reserved.
 * Licensed under: https://konn3kt.com/tos
 */
#pragma once

#define PQ_ML_MSG_AWV             0x366U
#define PQ_ML_MSG_ACC_SYSTEM      0x368U
#define PQ_ML_MSG_ACC_GRA_ANZEIGE 0x56AU
#define PQ_ML_MSG_MOTOR_2         0x288U
#define PQ_ML_MSG_GRA_NEU         0x38AU

#define PQ_ML_PT_BUS              1U
#define PQ_ML_CMD_TIMEOUT         25U
#define PQ_ML_ECHO_DEPTH          4U

bool pq_moduleless_armed = false;
bool pq_moduleless_emitting = false;
uint8_t pq_moduleless_tx_bus = 0U;

// Byte 1 high nibble is ACS_Sta_ADR; an OEM PQ radar idles at 2 (available, not requesting)
// and never at 0, which the drivetrain reads as the module being absent.
static const uint8_t PQ_ML_ACC_SYSTEM_IDLE[8] = {0U, 0x20U, 0x00U, 0xFEU, 0x07U, 0xFEU, 0xFEU, 0x00U};
static const uint8_t PQ_ML_ACC_HUD_IDLE[8] = {0U, 0U, 0x0CU, 0xFFU, 0x01U, 0x00U, 0x00U, 0x00U};
// Captured from an OEM PQ radar at idle: every Freigabe and warning bit clear, and
// ANB_Ziel_Teilbrems_Verz_Anf at raw 834, the encoding for exactly 0.000 m/s^2.
static const uint8_t PQ_ML_AWV_IDLE[8] = {0U, 0U, 0x00U, 0x74U, 0x00U, 0x00U, 0x80U, 0xD0U};

static bool pq_ml_ignition = false;
static bool pq_ml_latched_off = false;
static bool pq_ml_tsk_available = false;
static bool pq_ml_gra_coded = false;
static uint8_t pq_ml_acc_counter = 0U;
static uint8_t pq_ml_hud_counter = 0U;
static uint8_t pq_ml_awv_counter = 0U;
static uint8_t pq_ml_phase = 0U;
static uint32_t pq_ml_acc_age = PQ_ML_CMD_TIMEOUT;
static uint32_t pq_ml_hud_age = PQ_ML_CMD_TIMEOUT;
static uint8_t pq_ml_acc_payload[8];
static uint8_t pq_ml_hud_payload[8];
static uint8_t pq_ml_echo[PQ_ML_ECHO_DEPTH][8];
static uint8_t pq_ml_echo_idx = 0U;

static void pq_ml_copy(uint8_t *dst, const uint8_t *src) {
  for (uint8_t i = 0U; i < 8U; i++) {
    dst[i] = src[i];
  }
}

static void pq_ml_reset(void) {
  pq_moduleless_emitting = false;
  pq_ml_latched_off = false;
  pq_ml_tsk_available = false;
  pq_ml_gra_coded = false;
  pq_ml_acc_counter = 0U;
  pq_ml_hud_counter = 0U;
  pq_ml_awv_counter = 0U;
  pq_ml_phase = 0U;
  pq_ml_acc_age = PQ_ML_CMD_TIMEOUT;
  pq_ml_hud_age = PQ_ML_CMD_TIMEOUT;
  pq_ml_echo_idx = 0U;
  pq_ml_copy(pq_ml_acc_payload, PQ_ML_ACC_SYSTEM_IDLE);
  pq_ml_copy(pq_ml_hud_payload, PQ_ML_ACC_HUD_IDLE);
  for (uint8_t i = 0U; i < PQ_ML_ECHO_DEPTH; i++) {
    for (uint8_t j = 0U; j < 8U; j++) {
      pq_ml_echo[i][j] = 0U;
    }
  }
}

void pq_moduleless_configure(bool enabled, uint8_t tx_bus) {
  if (enabled != pq_moduleless_armed) {
    pq_ml_reset();
  }
  pq_moduleless_armed = enabled;
  pq_moduleless_tx_bus = enabled ? tx_bus : 0U;
}

void pq_moduleless_set_acc_payload(const uint8_t *data) {
  pq_ml_copy(pq_ml_acc_payload, data);
  pq_ml_acc_age = 0U;
}

void pq_moduleless_set_hud_payload(const uint8_t *data) {
  pq_ml_copy(pq_ml_hud_payload, data);
  pq_ml_hud_age = 0U;
}

void pq_moduleless_ignition_tick(bool ignition) {
  if (ignition != pq_ml_ignition) {
    pq_ml_reset();
  }
  pq_ml_ignition = ignition;
}

static void pq_ml_emit(uint32_t addr, const uint8_t *payload, bool counter_high_byte7, uint8_t *counter) {
  uint8_t data[8];
  pq_ml_copy(data, payload);
  if (counter_high_byte7) {
    data[7] = (uint8_t)((data[7] & 0x0FU) | (uint8_t)(*counter << 4));
  } else {
    data[1] = (uint8_t)((data[1] & 0xF0U) | *counter);
  }
  *counter = (uint8_t)((*counter + 1U) & 0x0FU);

  uint8_t checksum = 0U;
  for (uint8_t i = 1U; i < 8U; i++) {
    checksum ^= data[i];
  }
  data[0] = checksum;

  CANPacket_t msg = {0};
  msg.addr = addr;
  msg.bus = pq_moduleless_tx_bus;
  msg.data_len_code = 8U;
  pq_ml_copy(msg.data, data);
  can_set_checksum(&msg);
  can_send(&msg, pq_moduleless_tx_bus, true);

  if (addr == PQ_ML_MSG_ACC_SYSTEM) {
    pq_ml_copy(pq_ml_echo[pq_ml_echo_idx], data);
    pq_ml_echo_idx = (uint8_t)((pq_ml_echo_idx + 1U) % PQ_ML_ECHO_DEPTH);
  }
}

static bool pq_ml_is_own_echo(const CANPacket_t *msg) {
  bool match = false;
  for (uint8_t i = 0U; i < PQ_ML_ECHO_DEPTH; i++) {
    bool same = true;
    for (uint8_t j = 1U; j < 8U; j++) {
      if (pq_ml_echo[i][j] != msg->data[j]) {
        same = false;
      }
    }
    if (same) {
      match = true;
    }
  }
  return match;
}

void pq_moduleless_rx_hook(const CANPacket_t *msg) {
  if (pq_moduleless_armed && pq_ml_ignition && !pq_ml_latched_off) {
    if (msg->addr == PQ_ML_MSG_MOTOR_2) {
      pq_ml_tsk_available = GET_BIT(msg, 21U);
    }

    if (msg->addr == PQ_ML_MSG_GRA_NEU) {
      pq_ml_gra_coded = GET_BIT(msg, 15U);
    }

    // Our own ACC_System on the ECAN bus comes back on the powertrain bus through the J533
    // gateway, so a byte match against what we just sent is the only way to tell it apart from
    // a real module answering, which is the one case that must stop us.
    if ((msg->addr == PQ_ML_MSG_ACC_SYSTEM) && (!pq_moduleless_emitting || !pq_ml_is_own_echo(msg))) {
      pq_ml_latched_off = true;
      pq_moduleless_emitting = false;
    }

    const bool powertrain_tick = !pq_ml_latched_off && (msg->bus == PQ_ML_PT_BUS) &&
                                 (msg->addr == PQ_ML_MSG_MOTOR_2) && pq_ml_tsk_available && pq_ml_gra_coded;
    if (powertrain_tick) {
      pq_moduleless_emitting = true;

      if (pq_ml_acc_age < PQ_ML_CMD_TIMEOUT) {
        pq_ml_acc_age += 1U;
      } else {
        pq_ml_copy(pq_ml_acc_payload, PQ_ML_ACC_SYSTEM_IDLE);
      }
      if (pq_ml_hud_age < PQ_ML_CMD_TIMEOUT) {
        pq_ml_hud_age += 1U;
      } else {
        pq_ml_copy(pq_ml_hud_payload, PQ_ML_ACC_HUD_IDLE);
      }

      pq_ml_emit(PQ_ML_MSG_ACC_SYSTEM, pq_ml_acc_payload, false, &pq_ml_acc_counter);
      if (pq_ml_phase == 0U) {
        pq_ml_emit(PQ_ML_MSG_ACC_GRA_ANZEIGE, pq_ml_hud_payload, true, &pq_ml_hud_counter);
        pq_ml_emit(PQ_ML_MSG_AWV, PQ_ML_AWV_IDLE, false, &pq_ml_awv_counter);
      }
      pq_ml_phase ^= 1U;
    }
  }
}

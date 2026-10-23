// Copyright (c) 2026 IQ.Lvbs. All rights reserved.
#define CATCH_CONFIG_MAIN

#include "catch2/catch.hpp"
#include "selfdrive/pandad/pandad.h"

constexpr uint64_t SEC = 1000ULL * 1000ULL * 1000ULL;

TEST_CASE("ignition is off until the line has been seen high") {
  IgnitionLineHold hold;
  REQUIRE_FALSE(hold.update(false, false, 1 * SEC));
  REQUIRE_FALSE(hold.update(false, false, 10 * SEC));
}

TEST_CASE("a rising edge is reported immediately") {
  IgnitionLineHold hold;
  REQUIRE(hold.update(true, false, 1 * SEC));
}

TEST_CASE("dropouts shorter than the hold keep ignition on while the rx checks are valid") {
  IgnitionLineHold hold;
  REQUIRE(hold.update(true, false, 100 * SEC));
  REQUIRE(hold.update(false, false, 100 * SEC + SEC / 10));
  REQUIRE(hold.update(false, false, 102 * SEC + 3 * SEC / 10));
  REQUIRE(hold.update(true, false, 102 * SEC + 4 * SEC / 10));
  REQUIRE(hold.update(false, false, 102 * SEC + 5 * SEC / 10));
}

TEST_CASE("a loss lasting the full hold turns ignition off") {
  IgnitionLineHold hold;
  REQUIRE(hold.update(true, false, 100 * SEC));
  REQUIRE(hold.update(false, false, 100 * SEC + IGNITION_LINE_HOLD_NS - 1));
  REQUIRE_FALSE(hold.update(false, false, 100 * SEC + IGNITION_LINE_HOLD_NS));
  REQUIRE_FALSE(hold.update(false, false, 200 * SEC));
}

TEST_CASE("a car powering off ends ignition the moment the rx checks fail") {
  IgnitionLineHold hold;
  REQUIRE(hold.update(true, false, 100 * SEC));
  REQUIRE(hold.update(false, false, 100 * SEC + SEC / 5));
  REQUIRE(hold.update(false, false, 101 * SEC));
  REQUIRE_FALSE(hold.update(false, true, 101 * SEC + SEC / 10));
  REQUIRE_FALSE(hold.update(false, false, 101 * SEC + SEC / 5));
}

TEST_CASE("failed rx checks do not end ignition while the line is high") {
  IgnitionLineHold hold;
  REQUIRE(hold.update(true, false, 100 * SEC));
  REQUIRE(hold.update(true, true, 101 * SEC));
}

TEST_CASE("ignition comes back on the next high after a real loss") {
  IgnitionLineHold hold;
  REQUIRE(hold.update(true, false, 100 * SEC));
  REQUIRE_FALSE(hold.update(false, false, 110 * SEC));
  REQUIRE(hold.update(true, false, 111 * SEC));
}

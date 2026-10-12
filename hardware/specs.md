# Mosquito Laser Zapper — Hardware Specification Document

> ## ⚠️ SUPERSEDED IN PART — read this first
>
> This was the **v1** specification. Two things in it are wrong and were corrected
> after checking the primary literature:
>
> 1. **The laser selection is wrong.** This document specifies 5 mW / 100 mW at
>    650 nm. The measured lethal dose is **3 W at 25 ms** — roughly **30× more** —
>    and 650 nm red is the least efficient wavelength available. See the correction
>    in §4.1 and the full analysis in **`laser_power_and_safety.md`**.
> 2. **The detection architecture is wrong.** A single camera + ML model cannot see a
>    2 px insect or sample a 400–800 Hz wingbeat. See **`detector_head.md`** for the
>    IR-light-curtain design that replaces it.
>
> **Still accurate and worth using:** the wiring, GPIO assignments, power budget,
> assembly sequence, enclosure design, and the interlock chain. The interlock
> architecture in particular is sound and is carried forward into `detector_head.md`.

**Version:** 1.0 (partially superseded)
**Date:** 2026-10-11
**Target Controller:** Raspberry Pi 5 (primary) / Jetson Nano (alternative)
**Classification:** DIY / Experimental / Research Use Only

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component List & BOM](#2-component-list--bom)
3. [Wiring Diagram](#3-wiring-diagram)
4. [Safety Considerations & Interlocks](#4-safety-considerations--interlocks)
5. [Power Requirements & Battery Calculations](#5-power-requirements--battery-calculations)
6. [Assembly Instructions](#6-assembly-instructions)
7. [3D Printable Enclosure Design](#7-3d-printable-enclosure-design)
8. [Firmware & Software Interface](#8-firmware--software-interface)
9. [Testing & Validation](#9-testing--validation)
10. [Regulatory Compliance](#10-regulatory-compliance)

---

## 1. System Overview

The Mosquito Laser Zapper is an autonomous mosquito detection and elimination system using computer vision and a low-power red laser. The system detects mosquitoes via camera feed, tracks their position, and directs a focused 650nm laser beam to neutralize them in flight.

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  SYSTEM BLOCK DIAGRAM                    │
│                                                          │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │  Camera  │───▶│  Raspberry   │───▶│   Laser       │  │
│  │  Module  │    │  Pi 5        │    │   Diode       │  │
│  │ (IMX219) │    │  4GB         │    │   Module      │  │
│  └──────────┘    │              │    │   (650nm)     │  │
│                  │  - OpenCV    │    └───────┬───────┘  │
│                  │  - NumPy      │            │          │
│  ┌──────────┐    │  - TensorFlow │    ┌───────┴───────┐  │
│  │  IR LED  │───▶│  - Flask      │    │   Gimbal      │  │
│  │  Panel   │    │              │    │   (2x Servo)  │  │
│  │ (850nm)  │    │              │    └───────┬───────┘  │
│  └──────────┘    │              │            │          │
│                  └──────┬───────┘    ┌───────┴───────┐  │
│                         │            │  PCA9685      │  │
│                  ┌──────┴───────┐    │  Servo Driver │  │
│                  │   Power      │    └───────────────┘  │
│                  │   System     │                        │
│                  └──────────────┘                        │
└─────────────────────────────────────────────────────────┘
```

### Design Goals

- **Detection range:** 0.5–3 meters
- **Kill radius:** ~5mm focal spot at 1m
- **Response time:** <200ms from detection to laser fire
- **Operating time:** 4+ hours on battery
- **Weight:** <500g (excluding battery)
- **Cost target:** <$200 (BOM)

---

## 2. Component List & BOM

### 2.1 Main Controller

| # | Component | Part Number / SKU | Supplier | Qty | Unit Price (USD) | Total |
|---|-----------|-------------------|----------|-----|-------------------|-------|
| 1 | **Raspberry Pi 5 (4GB)** | SC1112 | Raspberry Pi Official / CanaKit | 1 | $110.00 | $110.00 |
| 2 | **microSD Card (64GB, A2)** | SDSQXCG-064G-GN6MA | SanDisk / Amazon | 1 | $12.99 | $12.99 |
| 3 | **Raspberry Pi 27W USB-C PSU** | SC1160 | Raspberry Pi Official / Waveshare | 1 | $11.99 | $11.99 |
| 4 | **Active Cooler for Pi 5** | SC1186 | Raspberry Pi Official / Amazon | 1 | $5.00 | $5.00 |

**Alternative Controller:** Nvidia Jetson Nano 4GB Developer Kit (~$150–$199, B01 version) — CUDA cores accelerate detection but 4GB RAM is limiting; Jetson Orin Nano Super 8GB ($249) recommended if budget allows.

### 2.2 Vision System

| # | Component | Part Number / SKU | Supplier | Qty | Unit Price (USD) | Total |
|---|-----------|-------------------|----------|-----|-------------------|-------|
| 5 | **Arducam 8MP IMX219 Camera** | B0275 | Arducam / Amazon | 1 | $9.99 | $9.99 |
| 6 | **Wide-Angle Lens (175° FOV)** | — | Amazon (third-party) | 1 | $26.99 | $26.99 |
| 7 | **Arducam IMX219 V3 Link Camera Kit** | B0415 | Arducam | 1 | $49.99 | $49.99 |
| 8 | **CSI Cable, 30cm** | — | Adafruit / Amazon | 1 | $3.50 | $3.50 |

### 2.3 Laser System

> ### ⛔ THIS TABLE IS WRONG — DO NOT BUY FROM IT
>
> The lethal dose for *Anopheles stephensi* is **3 W at 25 ms** (532 nm) —
> Keller et al., *Sci. Rep.* 6, 20936. The parts below are **~30× too weak** to kill
> anything, and **650 nm red is the least efficient wavelength available** (melanin
> absorbs red weakly, so required power goes *up*, not down).
>
> | | Power | Lethal? |
> |---|---|---|
> | 650 nm 5 mW (row 9) | 5 mW | **no** — this is a cat toy |
> | 650 nm 100 mW (rows 10–11) | 100 mW | **no** — ~1/30th of the required dose |
> | **what is actually needed** | **3 W @ 532 nm** | yes, at 25 ms |
> | **better still** | **2.1 W @ 10.6 µm CO₂** | yes, and absorbed before the retina |
>
> Use these rows only as a **Class 1/3R aiming pointer** to validate the tracking
> pipeline. Do not label any of them a "kill" component.
>
> Full analysis: **`laser_power_and_safety.md`**.

| # | Component | Part Number / SKU | Supplier | Qty | Unit Price (USD) | Total | Notes |
|---|-----------|-------------------|----------|-----|-------------------|-------|-------|
| 9 | 650nm 5mW Laser Diode Module (focusable, 3-5V) | — | Accio / AliExpress | 1 | $2.80 | $2.80 | ⛔ **aiming pointer only — not lethal** |
| 10 | 650nm 100mW Laser Diode Module | — | Accio / AliExpress | 1 | $8.50 | $8.50 | ⛔ **NOT a kill component — 30× too weak** |
| 11 | LPC836 650nm 100mW LD (bare diode, OEM) | LPC836 | LaserLand | 1 | $8.00 | $8.00 | ⛔ **NOT a kill component — wrong wavelength** |
| 12 | Brass Laser Housing (12mm) | — | eBay / AliExpress | 1 | $1.50 | $1.50 | |

**If you build a kill stage, the correct parts are:**

| Part | ~USD | Why |
|---|---|---|
| **10.6 µm CO₂ laser, 2.1 W** | 400+ | Comparable power to green, **absorbed before the retina** — exclusion zone collapses to <1 m |
| *or* 532 nm green, 3.0 W | 250+ | Most power-efficient, but needs a large exclusion zone |
| Heatsink + TEC | 60 | A 3 W source needs real thermal management a 100 mW diode does not |
| Driver rated for several amps | 40 | The MOSFET in §assembly is fine; the *ratings* are not |

### 2.4 Gimbal / Servo System

| # | Component | Part Number / SKU | Supplier | Qty | Unit Price (USD) | Total |
|---|-----------|-------------------|----------|-----|-------------------|-------|
| 13 | **MG996R Metal Gear Servo** | MG996R | Amazon / Adafruit / AliExpress | 2 | $7.75 | $15.50 |
| 14 | **PCA9685 16-Ch PWM Driver** | PCA9685 | Adafruit / ElectroPeak | 1 | $2.80 | $2.80 |
| 15 | **Servo Mounting Bracket Kit** | — | Amazon | 1 | $5.00 | $5.00 |

### 2.5 Illumination System

| # | Component | Part Number / SKU | Supplier | Qty | Unit Price (USD) | Total |
|---|-----------|-------------------|----------|-----|-------------------|-------|
| 16 | **850nm IR LED Module (3W)** | — | Amazon / AliExpress | 2 | $6.99 | $13.98 |
| 17 | **CHANZON 10mm IR LED (850nm)** | — | Amazon / DigiKey | 10 | $0.20 | $2.00 |
| 18 | **IR Illuminator Board (8-LED, 90°)** | — | Amazon | 1 | $29.99 | $29.99 |

### 2.6 Power System

| # | Component | Part Number / SKU | Supplier | Qty | Unit Price (USD) | Total |
|---|-----------|-------------------|----------|-----|-------------------|-------|
| 19 | **18650 Li-ion Battery (3400mAh)** | NCR18650B | Amazon / Panasonic | 4 | $5.50 | $22.00 |
| 20 | **18650 Battery Holder (4x)** | — | Amazon / Adafruit | 1 | $4.00 | $4.00 |
| 21 | **LM2596 Buck Converter Module** | LM2596S | Amazon / AliExpress | 2 | $0.90 | $1.80 |
| 22 | **IRLZ44N MOSFET (logic level)** | IRLZ44N | Amazon / DigiKey | 2 | $1.00 | $2.00 |
| 23 | **1N4007 Flyback Diode** | 1N4007 | DigiKey / Amazon | 5 | $0.10 | $0.50 |
| 24 | **1000µF 25V Electrolytic Cap** | — | SparkFun / Newark | 2 | $0.50 | $1.00 |
| 25 | **10kΩ Resistor (1/4W)** | — | Generic | 10 | $0.02 | $0.20 |
| 26 | **1kΩ Resistor (1/4W)** | — | Generic | 10 | $0.02 | $0.20 |
| 27 | **PCB Prototype Board** | — | Amazon | 1 | $5.00 | $5.00 |
| 28 | **Jumper Wire Kit** | — | Amazon | 1 | $6.00 | $6.00 |
| 29 | **5V 3A Relay Module** | — | Amazon | 1 | $2.00 | $2.00 |

### 2.7 Safety & Monitoring

| # | Component | Part Number / SKU | Supplier | Qty | Unit Price (USD) | Total |
|---|-----------|-------------------|----------|-----|-------------------|-------|
| 30 | **Microswitch (limit switch)** | V-15-1C25 | Omron / DigiKey | 2 | $1.50 | $3.00 |
| 31 | **Red LED Indicator (5mm)** | — | Generic | 3 | $0.10 | $0.30 |
| 32 | **Green LED Indicator (5mm)** | — | Generic | 3 | $0.10 | $0.30 |
| 33 | **Emergency Stop Button** | — | Amazon | 1 | $3.00 | $3.00 |
| 34 | **Thermistor (10k NTC)** | — | Generic | 1 | $0.20 | $0.20 |

### 2.8 Enclosure & Mounting

| # | Component | Part Number / SKU | Supplier | Qty | Unit Price (USD) | Total |
|---|-----------|-------------------|----------|-----|-------------------|-------|
| 35 | **PLA Filament (1kg, black)** | — | Amazon / Prusament | 1 | $20.00 | $20.00 |
| 36 | **Acrylic Sheet (3mm, clear)** | — | Local / Amazon | 1 | $8.00 | $8.00 |
| 37 | **M3 Screw & Nut Set** | — | Amazon | 1 set | $6.00 | $6.00 |
| 38 | **Hex Standoff Kit (M2.5)** | — | Amazon | 1 set | $4.00 | $4.00 |
| 39 | **Rubber Feet (anti-vibration)** | — | Amazon | 1 set | $3.00 | $3.00 |
| 40 | **Prototype Enclosure Box** | — | Amazon | 1 | $12.00 | $12.00 |

### BOM Summary

| Category | Subtotal (USD) |
|----------|----------------|
| Main Controller | $139.98 |
| Vision System | $90.47 |
| Laser System | $20.80 |
| Gimbal / Servo | $23.30 |
| Illumination | $45.97 |
| Power System | $44.00 |
| Safety & Monitoring | $6.80 |
| Enclosure & Mounting | $53.00 |
| **TOTAL** | **$424.32** |

> **Note:** Prices sourced from Amazon, AliExpress, Accio, Arducam, Adafruit, ElectroPeak, SparkFun, Newark, and Raspberry Pi official retailers as of October 2026. Prices fluctuate; verify before ordering. Raspberry Pi 5 prices have increased significantly (4GB now $110 vs $60 at launch) due to LPDDR4 DRAM shortages. The Jetson Orin Nano Super 8GB ($249) is a viable alternative with 67 TOPS AI performance if budget allows.

---

## 3. Wiring Diagram

### 3.1 System Interconnect (ASCII)

```
                    ┌─────────────────────────────┐
                    │      RASPBERRY PI 5          │
                    │      40-Pin GPIO Header      │
                    │                              │
                    │  Pin 1  (3.3V) ──────┐       │
                    │  Pin 2  (5V) ────────┼──┐    │
                    │  Pin 3  (GPIO2/SDA) ─┼┐ │    │
                    │  Pin 4  (5V) ────────┼┼┐│    │
                    │  Pin 5  (GPIO3/SCL) ─┼┼┼┘    │
                    │  Pin 6  (GND) ───────┼┼┼──┐  │
                    │  Pin 7  (GPIO4) ─────┼┼┼──┤  │
                    │  Pin 9  (GND) ───────┼┼┼──┤  │
                    │  Pin 11 (GPIO17) ────┼┼┼──┤  │
                    │  Pin 12 (GPIO18) ────┼┼┼──┤  │
                    │  Pin 13 (GPIO27) ────┼┼┼──┤  │
                    │  Pin 14 (GND) ───────┼┼┼──┤  │
                    │  Pin 15 (GPIO22) ────┼┼┼──┤  │
                    │  Pin 16 (GPIO23) ────┼┼┼──┤  │
                    │  Pin 17 (3.3V) ──────┼┼┼──┤  │
                    │  Pin 18 (GPIO24) ────┼┼┼──┤  │
                    │  Pin 19 (GPIO10/MOSI)┼┼┼──┤  │
                    │  Pin 21 (GPIO9/MISO) ┼┼┼──┤  │
                    │  Pin 22 (GPIO25) ────┼┼┼──┤  │
                    │  Pin 23 (GPIO8/CE0) ┼┼┼──┤  │
                    │  Pin 24 (GPIO7/CE1) ┼┼┼──┤  │
                    │  Pin 26 (GPIO5) ────┼┼┼──┤  │
                    │  Pin 27 (GPIO6) ────┼┼┼──┤  │
                    │  Pin 28 (GPIO12) ───┼┼┼──┤  │
                    │  Pin 29 (GPIO5) ────┼┼┼──┤  │
                    │  Pin 31 (GPIO6) ────┼┼┼──┤  │
                    │  Pin 32 (GPIO12) ───┼┼┼──┤  │
                    │  Pin 33 (GPIO13) ───┼┼┼──┤  │
                    │  Pin 34 (GND) ──────┼┼┼──┤  │
                    │  Pin 35 (GPIO19) ───┼┼┼──┤  │
                    │  Pin 36 (GPIO16) ───┼┼┼──┤  │
                    │  Pin 37 (GPIO26) ───┼┼┼──┤  │
                    │  Pin 38 (GPIO20) ───┼┼┼──┤  │
                    │  Pin 39 (GND) ──────┼┼┼──┤  │
                    │  Pin 40 (GPIO21) ───┼┼┼──┘  │
                    └──────────────────────┼┼┼─────┘
                                           │││
              ┌────────────────────────────┼┼┼────────────────────────────┐
              │                            │││                             │
              │  ┌─────────────────────┐   │││   ┌─────────────────────┐   │
              │  │   PCA9685 Servo     │   │││   │   LM2596 Buck       │   │
              │  │   Driver Board      │   │││   │   Converter #1      │   │
              │  │                     │   │││   │   (5V output)       │   │
              │  │  VCC ─── Pin 2 (5V) │◄──┘││   │                     │   │
              │  │  GND ─── Pin 6 (GND) │◄───┘│   │  VIN ── Battery    │   │
              │  │  SDA ─── Pin 3 (SDA) │◄────┘   │  VOUT ── Pi 5V     │   │
              │  │  SCL ─── Pin 5 (SCL) │◄────────│  GND ── Common    │   │
              │  │                     │         │                     │   │
              │  │  V+ ── Battery 5V   │         └─────────────────────┘   │
              │  │  GND ── Common GND   │                                     │
              │  │                     │         ┌─────────────────────┐   │
              │  │  PWM0 ── Servo X    │         │   LM2596 Buck       │   │
              │  │  PWM1 ── Servo Y    │         │   Converter #2      │   │
              │  │  V+ ─── 5V Rail     │         │   (3.3V output)     │   │
              │  │  GND ── GND Rail    │         │                     │   │
              │  └─────────┬───────────┘         │  VIN ── Battery    │   │
              │            │                     │  VOUT ── 3.3V Rail │   │
              │  ┌─────────┴───────────┐         └─────────────────────┘   │
              │  │   GIMBAL ASSEMBLY   │                                     │
              │  │                     │                                     │
              │  │  Servo X (azimuth)  │                                     │
              │  │  ┌───────────────┐  │         ┌─────────────────────┐   │
              │  │  │  MG996R       │  │         │   IR LED MODULE     │   │
              │  │  │  (Horizontal) │  │         │   850nm 3W         │   │
              │  │  └───────┬───────┘  │         │                     │   │
              │  │          │          │         │  VCC ─── 5V Rail    │   │
              │  │  Servo Y (elevation)│        │  GND ─── Common GND  │   │
              │  │  ┌───────────────┐  │         └─────────────────────┘   │
              │  │  │  MG996R       │  │                                     │
              │  │  │  (Vertical)   │  │         ┌─────────────────────┐   │
              │  │  └───────────────┘  │         │   LASER DIODE       │   │
              │  │                     │         │   650nm 100mW       │   │
              │  └─────────────────────┘         │                     │   │
              │                                  │  VCC ─── MOSFET     │   │
              │  ┌─────────────────────┐         │  GND ─── Common GND  │   │
              │  │   LASER MODULE      │         └──────────┬──────────┘   │
              │  │                     │                    │              │
              │  │  ┌───────────────┐  │         ┌──────────┴──────────┐   │
              │  │  │  IRLZ44N      │  │         │   1000µF Cap       │   │
              │  │  │  MOSFET       │  │         │   (filter)          │   │
              │  │  │  Gate ─ GPIO18│  │         │   + ─── 5V Rail     │   │
              │  │  │  Drain ─ Laser│  │         │   - ─── Common GND  │   │
              │  │  │  Source ─ GND │  │         └─────────────────────┘   │
              │  │  └───────────────┘  │                                     │
              │  │                     │         ┌─────────────────────┐   │
              │  │  1N4007 Diode      │         │   MICROSWITCH       │   │
              │  │  (flyback, across  │         │   (interlock)       │   │
              │  │   laser terminals) │         │                     │   │
              │  └─────────────────────┘         │  COM ─── GPIO27    │   │
              │                                  │  NO  ─── 3.3V       │   │
              │  ┌─────────────────────┐         └─────────────────────┘   │
              │  │   18650 BATTERY    │                                     │
              │  │   PACK (4S1P)      │         ┌─────────────────────┐   │
              │  │   14.8V 3400mAh    │         │   STATUS LEDS       │   │
              │  │                     │         │                     │   │
              │  │  + ─── VIN (LM2596)│         │  Red ─── GPIO22     │   │
              │  │  - ─── Common GND  │         │  Green ── GPIO23    │   │
              │  └─────────────────────┘         │                     │   │
              │                                  │  (with 1kΩ resistor)│   │
              └──────────────────────────────────┴─────────────────────┘   │
```

### 3.2 GPIO Pin Assignment Table

| GPIO Pin | Physical Pin | Function | Connected To |
|----------|-------------|----------|-------------|
| 3.3V | Pin 1 | 3.3V Power | PCA9685 VCC, Status LEDs (via resistor), Microswitch NO |
| 5V | Pin 2 | 5V Power | PCA9685 V+, IR LED VCC, Servo V+, Laser MOSFET drain (via buck) |
| GPIO 2 (SDA) | Pin 3 | I2C Data | PCA9685 SDA |
| 5V | Pin 4 | 5V Power | (spare) |
| GPIO 3 (SCL) | Pin 5 | I2C Clock | PCA9685 SCL |
| GND | Pin 6 | Ground | Common ground bus |
| GPIO 4 | Pin 7 | GPCLK0 | (spare / status beacon) |
| GPIO 14 (TXD) | Pin 8 | UART TX | (debug console) |
| GND | Pin 9 | Ground | (spare) |
| GPIO 15 (RXD) | Pin 10 | UART RX | (debug console) |
| GPIO 17 | Pin 11 | PWM/IO | Emergency stop input (active LOW) |
| GPIO 18 | Pin 12 | PWM | IRLZ44N MOSFET Gate (laser enable) |
| GPIO 27 | Pin 13 | IO | Microswitch interlock input |
| GND | Pin 14 | Ground | Common ground bus |
| GPIO 22 | Pin 15 | IO | Red status LED (via 1kΩ) |
| GPIO 23 | Pin 16 | IO | Green status LED (via 1kΩ) |
| 3.3V | Pin 17 | 3.3V Power | (spare) |
| GPIO 24 | Pin 18 | IO | (spare / future expansion) |
| GPIO 10 (MOSI) | Pin 19 | SPI MOSI | (spare) |
| GND | Pin 20 | Ground | (spare) |
| GPIO 9 (MISO) | Pin 21 | SPI MISO | (spare) |
| GPIO 25 | Pin 22 | IO | (spare) |
| GPIO 8 (CE0) | Pin 23 | SPI CE0 | (spare) |
| GND | Pin 24 | Ground | (spare) |
| GPIO 7 (CE1) | Pin 25 | SPI CE1 | (spare) |
| GPIO 5 | Pin 26 | IO | (spare) |
| GND | Pin 27 | Ground | (spare) |
| GPIO 6 | Pin 28 | IO | (spare) |
| GPIO 12 | Pin 29 | PWM0 | (spare / buzzer) |
| GND | Pin 30 | Ground | (spare) |
| GPIO 13 | Pin 31 | PWM1 | (spare / buzzer) |
| GPIO 19 | Pin 33 | IO | (spare) |
| GND | Pin 34 | Ground | (spare) |
| GPIO 16 | Pin 36 | IO | (spare) |
| GPIO 26 | Pin 37 | IO | (spare) |
| GPIO 20 | Pin 38 | IO | (spare) |
| GND | Pin 39 | Ground | (spare) |
| GPIO 21 | Pin 40 | IO | (spare) |

### 3.3 PCA9685 Servo Connections

```
PCA9685 Channel    Servo           Wire Color
─────────────────────────────────────────────
PWM0              Servo X (Pan)    Orange (signal) / Red (VCC) / Brown (GND)
PWM1              Servo Y (Tilt)   Orange (signal) / Red (VCC) / Brown (GND)
V+                5V Power Rail    Red
GND               Ground Rail      Black/Brown
SDA               Pi GPIO 2 (SDA)  Blue
SCL               Pi GPIO 3 (SCL)  Green
VCC               3.3V             Red
```

---

## 4. Safety Considerations & Interlocks

### 4.1 Laser Safety Classification

> ## ⛔ CORRECTION — READ BEFORE TRUSTING THIS SECTION
>
> **The laser selection in this document (sections 1, 2.3, 4.1, 5) is wrong and has
> been superseded. Do not build the laser stage from this spec.**
>
> **Defect 1 — the power is ~30× too low to kill anything.**
> This document specifies **5 mW** (default) and **100 mW** ("upgraded kill"). The
> measured lethal dose for *Anopheles stephensi* is **3 W at 25 ms** (532 nm) —
> Keller et al., *Sci. Rep.* 6, 20936 (2016). A 100 mW continuous beam delivers
> roughly **1/30th** of the required energy per unit area. It will not kill a
> mosquito; it will warm it slightly.
>
> **Defect 2 — 650 nm red is the worst available wavelength choice.**
> Mosquito tissue is melanin-pigmented, and melanin absorbs green/blue strongly and
> red weakly. The same paper measured LD90 fluence at 1064 nm as ~2× that of 532 nm
> for this reason. Red pushes the required power *up*, not down.
>
> **Defect 3 — a 5 mW "default" cannot even prove the pipeline.**
> Section 1 describes the system as neutralising mosquitoes "in flight" with a
> 650 nm beam. At 5 mW that is not achievable, and at 100 mW it is still not
> achievable. The demo value of a low-power beam is that it validates *aiming* — the
> document should have said that plainly.
>
> **What is actually correct:**
>
> | Wavelength | Lethal power (25 ms) | Eye hazard |
> |---|---|---|
> | 532 nm green | **3.0 W** | serious — reaches the retina |
> | 10.6 µm CO₂ | **2.1 W** | **contained** — absorbed before the retina |
> | 1.47 µm | 7.9 W | contained |
> | 976 nm diode | 10.9 W | serious |
> | 650 nm red (this doc) | not lethal at any sane power | serious |
>
> **If you build a kill stage, use 10.6 µm CO₂ or 1.47 µm** — comparable power to
> green and absorbed before the retina, which collapses the exclusion zone from
> "most of the room" to under a metre.
>
> Full analysis: **`laser_power_and_safety.md`**.
>
> **The rest of this document remains useful** — the wiring, power budget, assembly
> sequence, enclosure design and interlock chain are sound engineering, and the
> interlock chain is exactly the right architecture. Only the laser selection is
> wrong. Substitute the wavelength and power, then scale the driver and heatsinking
> accordingly (a 3 W source needs real thermal management that a 100 mW diode does
> not).

### 4.1.1 Laser Safety Classification (as originally written — see correction above)

| Parameter | Value |
|-----------|-------|
| Wavelength | 650nm (visible red) |
| Output Power | 5mW (Class 3R) — default |
| Output Power | 100mW (Class 3B) — upgraded |
| Beam Divergence | <1.5 mrad |
| NOHD (5mW) | ~3m |
| NOHD (100mW) | ~10m |

> **⚠️ WARNING:** Class 3B lasers (100mW) can cause immediate eye damage upon direct or reflected exposure. The system MUST include all interlocks listed below. Never operate the 100mW configuration without the enclosure closed and interlock engaged.
>
> Note this warning is *correct* and applies with far more force to the 3 W source
> actually required. The NOHD figures above scale with power: at 3 W the nominal
> ocular hazard distance is roughly **30× further** than the 100 mW figure shown.

### 4.2 Mandatory Safety Interlocks

```
┌─────────────────────────────────────────────────────────────┐
│              INTERLOCK CHAIN (all must be TRUE)              │
│                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐  │
│  │ Enclosure │   │ E-Stop   │   │ Proximity│   │ Dead Man │  │
│  │ Closed    │──▶│ NOT      │──▶│ Sensor   │──▶│ Switch   │  │
│  │ (switch)  │   │ Pressed  │   │ Clear    │   │ Held     │  │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘  │
│       │              │              │              │         │
│       └──────────────┴──────────────┴──────────────┘         │
│                          │                                    │
│                          ▼                                    │
│                   ┌──────────┐                               │
│                   │  AND     │                               │
│                   │  Gate    │                               │
│                   │ (GPIO27) │                               │
│                   └────┬─────┘                               │
│                        │                                     │
│                        ▼                                     │
│                ┌──────────────┐                              │
│                │ Laser Enable │                              │
│                │ (MOSFET Gate)│                              │
│                └──────────────┘                              │
└─────────────────────────────────────────────────────────────┘
```

#### Interlock 1: Enclosure Closed Switch

- **Type:** Microswitch (limit switch)
- **Part:** Omron V-15-1C25 or equivalent
- **GPIO:** Pin 13 (GPIO 27)
- **Logic:** When enclosure lid is closed, switch is pressed → GPIO reads LOW (pull-down) or HIGH depending on wiring
- **Failure mode:** If switch fails open, laser is disabled (fail-safe)

#### Interlock 2: Emergency Stop Button

- **Type:** Normally Closed (NC) momentary pushbutton
- **Wiring:** In series with the 5V laser power rail
- **Logic:** Press button → physically breaks power to laser module
- **GPIO:** Pin 11 (GPIO 17) — also monitored by software for graceful shutdown
- **Failure mode:** NC contact opens → laser power cut

#### Interlock 3: Proximity / Motion Sensor (Optional)

- **Type:** PIR sensor or ultrasonic distance sensor
- **Purpose:** Detect human/animal presence near the laser aperture
- **GPIO:** Pin 16 (GPIO 23)
- **Logic:** If motion detected within 2m of aperture → disable laser

#### Interlock 4: Dead Man's Switch (Software)

- **Type:** Software watchdog
- **Logic:** Laser can only fire while a "fire" condition is actively held (target locked). If tracking is lost for >100ms, laser is disabled until re-acquisition.

### 4.3 Optical Safety

```
┌──────────────────────────────────────────────────────┐
│              LASER APERTURE DESIGN                    │
│                                                       │
│   ┌─────────────────────────────────────────────┐    │
│   │                                             │    │
│   │    ┌─────────┐                              │    │
│   │    │  Laser  │──────▶ Beam passes through   │    │
│   │    │  Module │        this opening only     │    │
│   │    └─────────┘                              │    │
│   │         │                                   │    │
│   │    ┌────┴────┐                              │    │
│   │    │  Beam   │  Internal baffle to block    │    │
│   │    │  Baffle │  stray reflections            │    │
│   │    └─────────┘                              │    │
│   │                                             │    │
│   │    Enclosure walls: matte black interior     │    │
│   │    to absorb scattered laser light           │    │
│   │                                             │    │
│   └─────────────────────────────────────────────┘    │
│                                                       │
│   Safety requirements:                                │
│   - Aperture diameter: 15mm (fits laser housing)     │
│   - All internal surfaces: matte black anodized       │
│   - Beam path: enclosed for full internal length      │
│   - Warning label: "CLASS 3B LASER — DO NOT STARE    │
│     INTO BEAM" affixed to enclosure exterior          │
└──────────────────────────────────────────────────────┘
```

### 4.4 Thermal Safety

- **Laser diode:** Maximum operating temperature 60°C. Brass housing acts as heatsink. Add thermal pad between laser housing and enclosure mount.
- **Raspberry Pi 5:** Active cooler required (heatsink + fan). Temperature monitoring via `vcgencmd measure_temp`. If >80°C, system should throttle and eventually disable laser.
- **MOSFET (IRLZ44N):** Rds(on) = 0.022Ω. At 100mW laser draw (~200mA), power dissipation = I²R = 0.04 × 0.022 = 0.88mW — negligible, no heatsink needed.

### 4.5 Electrical Safety

- **Flyback diode:** 1N4007 across laser diode terminals (cathode to +V, anode to GND) to suppress inductive kickback when MOSFET switches off.
- **Decoupling capacitor:** 1000µF 25V electrolytic across the 5V rail near the laser to handle transient current spikes.
- **Fuse:** 2A fast-blow fuse on battery pack output.
- **Reverse polarity protection:** Diode in series with battery input or P-channel MOSFET polarity protection circuit.
- **Grounding:** All grounds tied to common star ground point.

### 4.6 Operational Safety Protocol

1. **Pre-operation checklist:**
   - [ ] Enclosure closed and interlock engaged
   - [ ] E-Stop button accessible and functional
   - [ ] Warning labels visible
   - [ ] No personnel within NOHD
   - [ ] Laser aperture pointing in safe direction
   - [ ] Battery charge adequate

2. **During operation:**
   - [ ] System status LED shows GREEN (ready) or RED (disabled/fault)
   - [ ] Never open enclosure while powered
   - [ ] If E-Stop pressed, wait 30 seconds before investigating

3. **Post-operation:**
   - [ ] Power off system
   - [ ] Wait 5 minutes for capacitors to discharge
   - [ ] Store in secure location

---

## 5. Power Requirements & Battery Calculations

### 5.1 Component Power Consumption

| Component | Voltage | Current (mA) | Power (W) |
|-----------|---------|-------------|-----------|
| Raspberry Pi 5 (idle) | 5V | 600 | 3.00 |
| Raspberry Pi 5 (peak) | 5V | 1200 | 6.00 |
| Raspberry Pi 5 (with camera) | 5V | 800 | 4.00 |
| Camera Module (IMX219) | 3.3V/5V | 250 | 1.25 |
| IR LED Module (2x 3W) | 5V | 1400 | 7.00 |
| Laser Diode (5mW) | 5V | 100 | 0.50 |
| Laser Diode (100mW) | 5V | 300 | 1.50 |
| MG996R Servo (idle) | 5V | 100 | 0.50 |
| MG996R Servo (moving) | 5V | 500 | 2.50 |
| MG996R Servo (stall) | 5V | 1200 | 6.00 |
| PCA9685 | 5V | 10 | 0.05 |
| LM2596 (quiescent) | — | 5 | 0.025 |
| **Total (typical, 5mW laser)** | | **~3,500** | **~17.5W** |
| **Total (typical, 100mW laser)** | | **~3,700** | **~18.5W** |
| **Peak (both servos stall + 100mW)** | | **~5,700** | **~28.5W** |

### 5.2 Battery Options

#### Option A: 18650 Li-ion Pack (4S1P)

| Parameter | Value |
|-----------|-------|
| Configuration | 4 cells in series, 1 parallel |
| Nominal Voltage | 14.8V (4 × 3.7V) |
| Fully Charged | 16.8V (4 × 4.2V) |
| Capacity | 3,400 mAh |
| Energy | 50.3 Wh |
| BMS Required | Yes (4S BMS board, $3-5) |
| Runtime (17.5W draw) | 50.3 / 17.5 ≈ **2.9 hours** |
| Runtime (18.5W draw) | 50.3 / 18.5 ≈ **2.7 hours** |
| Weight | ~200g (cells) + 50g (BMS) |
| Cost | ~$25 (cells + BMS + holder) |

#### Option B: 18650 Li-ion Pack (4S2P)

| Parameter | Value |
|-----------|-------|
| Configuration | 4 series, 2 parallel |
| Nominal Voltage | 14.8V |
| Capacity | 6,800 mAh |
| Energy | 100.6 Wh |
| Runtime (17.5W draw) | 100.6 / 17.5 ≈ **5.7 hours** |
| Runtime (18.5W draw) | 100.6 / 18.5 ≈ **5.4 hours** |
| Weight | ~400g |
| Cost | ~$50 |

#### Option C: USB Power Bank (5V)

| Parameter | Value |
|-----------|-------|
| Output | 5V, 3A (max) |
| Capacity | 20,000 mAh (74 Wh) |
| Runtime (17.5W draw) | 74 / 17.5 ≈ **4.2 hours** |
| Pros | Simple, no BMS needed, portable |
| Cons | Only 5V output — servos run at reduced torque; high current draw |

#### Option D: LiPo Drone Battery (3S)

| Parameter | Value |
|-----------|-------|
| Configuration | 3S LiPo |
| Nominal Voltage | 11.1V |
| Capacity | 2,200 mAh |
| Energy | 24.4 Wh |
| Runtime (17.5W draw) | 24.4 / 17.5 ≈ **1.4 hours** |
| Pros | Lightweight, high discharge rate |
| Cons | Short runtime, requires LiPo-specific charger |

### 5.3 Recommended Power Topology

```
┌──────────────────────────────────────────────────────────────────┐
│                    POWER DISTRIBUTION                            │
│                                                                  │
│  ┌─────────────┐                                                │
│  │ 18650 Pack  │ 14.8V                                          │
│  │ (4S1P BMS)  │                                                │
│  └──────┬──────┘                                                │
│         │                                                        │
│         ├──────────────────────────────────────┐                │
│         │                                      │                │
│         ▼                                      ▼                │
│  ┌─────────────┐                      ┌─────────────┐           │
│  │ LM2596 #1   │                      │ LM2596 #2   │           │
│  │ 14.8V→5V    │                      │ 14.8V→5V    │           │
│  │ 3A output   │                      │ 3A output   │           │
│  └──────┬──────┘                      └──────┬──────┘           │
│         │                                    │                   │
│         │  5V Rail A                         │  5V Rail B        │
│         │  (servos, IR, laser)               │  (Pi, camera)     │
│         │                                    │                   │
│         ▼                                    ▼                   │
│  ┌─────────────┐                      ┌─────────────┐           │
│  │ Servo V+    │                      │ Pi 5V (PSU) │           │
│  │ IR LED VCC  │                      │ Camera VCC  │           │
│  │ Laser VCC   │                      │ PCA9685 VCC │           │
│  │ (via MOSFET)│                      │ (3.3V pin)  │           │
│  └─────────────┘                      └─────────────┘           │
│                                                                  │
│  ┌─────────────────────────────────────────────────┐            │
│  │ 1000µF electrolytic caps on each 5V rail        │            │
│  │ 100nF ceramic caps on each IC power pin         │            │
│  └─────────────────────────────────────────────────┘            │
│                                                                  │
│  ┌─────────────────────────────────────────────────┐            │
│  │ Common GND bus (star ground topology)           │            │
│  │ Fuse: 2A on battery output                       │            │
│  └─────────────────────────────────────────────────┘            │
└──────────────────────────────────────────────────────────────────┘
```

### 5.4 Voltage Regulator Sizing

| Regulator | Input | Output | Max Current | Load |
|-----------|-------|--------|-------------|------|
| LM2596 #1 | 14.8V | 5V | 3A (15W) | Servos (2.5A peak), IR LEDs (1.4A), Laser (0.3A) = 4.2A peak → **INSUFFICIENT** |
| LM2596 #2 | 14.8V | 5V | 3A (15W) | Pi (1.2A), Camera (0.25A), PCA9685 (0.01A) = 1.5A → **OK** |

> **⚠️ IMPORTANT:** For the 100mW laser configuration with two servos active, the 5V rail A requires >4A. Use a single **XL4015** (5A buck converter) or **two LM2596 in parallel** (with current-sharing resistors) for rail A. Alternatively, use a 6S LiPo pack (22.2V) with a higher-current buck converter.

**Revised power system for 100mW configuration:**

| Regulator | Part | Input | Output | Current | Cost |
|-----------|------|-------|--------|---------|------|
| Buck #1 | XL4015 5A | 14.8V | 5V | 5A (25W) | $3.50 |
| Buck #2 | LM2596 3A | 14.8V | 5V | 3A (15W) | $0.90 |

### 5.5 Battery Runtime Calculation (Detailed)

**Assumptions:**
- Average system draw (typical operation, 100mW laser, IR on, tracking): 18W
- Battery: 4S1P 18650, 3,400 mAh, 50.3 Wh usable (80% depth of discharge)
- LM2596 efficiency: 85%
- Usable energy: 50.3 × 0.80 = 40.2 Wh

**Runtime = 40.2 Wh / 18W = 2.23 hours ≈ 2 hours 14 minutes**

**With 4S2P (6,800 mAh, 100.6 Wh):**
- Usable energy: 100.6 × 0.80 = 80.5 Wh
- Runtime = 80.5 / 18 = 4.47 hours ≈ 4 hours 28 minutes

---

## 6. Assembly Instructions

### 6.1 Tools Required

- [ ] Soldering iron (temperature controlled, 350°C)
- [ ] Solder wire (0.8mm, rosin core)
- [ ] Flux pen
- [ ] Wire strippers
- [ ] Multimeter
- [ ] Screwdriver set (Phillips #0, #1)
- [ ] Needle-nose pliers
- [ ] Heat shrink tubing (assorted sizes)
- [ ] Electrical tape
- [ ] Hot glue gun
- [ ] Caliper (for measuring)

### 6.2 Assembly Steps

#### Step 1: Prepare the Raspberry Pi 5

1. Flash microSD card with Raspberry Pi OS Lite (64-bit) using Raspberry Pi Imager.
2. Enable SSH, I2C, SPI, and Camera via `raspi-config`:
   ```bash
   sudo raspi-config
   # Interface Options → Enable I2C, SPI, Camera
   ```
3. Install required packages:
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y python3-pip python3-opencv python3-numpy \
       libcamera-dev python3-libcamera python3-kms++ \
       python3-picamera2 python3-flask python3-smbus i2c-tools
   pip3 install adafruit-circuitpython-servokit RPi.GPIO
   ```
4. Attach active cooler to Pi 5 CPU.
5. Connect camera CSI cable to Pi 5 camera port (blue side facing USB ports).

#### Step 2: Assemble the PCA9685 Servo Driver

1. Solder male header pins to PCA9685 board (if not pre-soldered).
2. Connect PCA9685 to Pi 5:
   - VCC → Pin 1 (3.3V)
   - GND → Pin 6 (GND)
   - SDA → Pin 3 (GPIO2/SDA)
   - SCL → Pin 5 (GPIO3/SCL)
3. Verify I2C detection:
   ```bash
   sudo i2cdetect -y 1
   # Should show address 0x40 (default PCA9685 address)
   ```

#### Step 3: Wire the Servos

1. Connect Servo X (pan) to PCA9685 PWM0:
   - Signal (orange) → PWM0 pin
   - VCC (red) → V+ pin (external 5V)
   - GND (brown) → GND pin
2. Connect Servo Y (tilt) to PCA9685 PWM1:
   - Signal (orange) → PWM1 pin
   - VCC (red) → V+ pin (external 5V)
   - GND (brown) → GND pin
3. **DO NOT power servos from Pi 5V pins** — use external 5V supply from LM2596.

#### Step 4: Build the Laser Driver Circuit

1. On a prototype PCB, build the MOSFET switch circuit:
   ```
   Battery 14.8V ──┬── XL4015 VIN (+)
                   │
                   ├── LM2596 VIN (+)
                   │
                   └── 2A Fuse ──┬── XL4015 VIN (+)
                                │
   Common GND ───────┬──────────┴── XL4015 VIN (-)
                     │
                     ├── LM2596 VIN (-)
                     │
                     ├── IRLZ44N Source
                     │
                     ├── 1N4007 Cathode
                     │
                     ├── 1000µF (-)
                     │
                     └── Pi GND (Pin 6)
   
   XL4015 VOUT (+) ──┬── 1000µF (+)
                     │
                     ├── IRLZ44N Drain
                     │
                     ├── Laser Module VCC (+)
                     │
                     └── 1N4007 Anode (wait, this is wrong)
   ```

   **Correct laser driver circuit:**

   ```
   5V Rail (from XL4015) ──┬── 1000µF electrolytic (+)
                           │
                           ├── Laser Diode Anode (+)
                           │
                           └── 1N4007 Cathode (band end)
   
   IRLZ44N Drain ──────────── Laser Diode Cathode (-)
   
   IRLZ44N Source ─────────── Common GND
   
   IRLZ44N Gate ──────────── Pi GPIO18 (Pin 12) via 1kΩ resistor
   
   1N4007 Anode ──────────── Common GND
   
   1000µF (-) ────────────── Common GND
   ```

2. Solder components on prototype board.
3. Add 1kΩ resistor between GPIO18 and MOSFET gate.
4. Add 10kΩ pull-down resistor from gate to ground (ensures laser off at boot).
5. Test with multimeter: verify 5V on rail, 0V at gate (laser off), 3.3V at gate (laser on).

#### Step 5: Install IR LEDs

1. Mount IR LED modules on either side of the camera lens.
2. Wire IR LED VCC to 5V rail (from XL4015 or LM2596 #1).
3. Wire IR LED GND to common ground.
4. Add 1000µF capacitor across IR LED power terminals.
5. Test: power on, verify IR illumination using phone camera (should see purple glow).

#### Step 6: Assemble the Gimbal

1. Mount Servo X (pan) to the base plate using M3 screws and servo bracket.
2. Attach Servo Y (tilt) horn to the laser module mount using M2 screws.
3. Mount the laser module (in brass housing) to the tilt bracket.
4. Ensure full range of motion:
   - Pan: 0° to 180° (or continuous rotation if modified)
   - Tilt: 0° to 180°
5. Secure all wiring with zip ties to prevent tangling during rotation.

#### Step 7: Wire Safety Interlocks

1. Mount microswitch on enclosure lid such that it closes when lid is shut.
2. Wire microswitch:
   - COM → GPIO27 (Pin 13)
   - NO → 3.3V (Pin 1) with 10kΩ pull-down to GND
3. Wire E-Stop button:
   - NC contact in series with 5V laser power rail
   - NO contact to GPIO17 (Pin 11) with 10kΩ pull-down to GND
4. Wire status LEDs:
   - Red LED: Anode → 1kΩ → GPIO22, Cathode → GND
   - Green LED: Anode → 1kΩ → GPIO23, Cathode → GND

#### Step 8: Power System Assembly

1. Solder 4x 18650 cells in series (4S1P) using nickel strip and spot welder (or solder with care).
2. Connect 4S BMS board to cell balance leads and main power leads.
3. Connect BMS output to battery holder / XT60 connector.
4. Connect battery output to LM2596/XL4015 input (through 2A fuse).
5. Adjust LM2596 output to exactly 5.0V using multimeter.
6. Adjust XL4015 output to exactly 5.0V using multimeter.
7. Connect LM2596 output to Pi 5V and camera 5V.
8. Connect XL4015 output to servo V+, IR LED VCC, and laser MOSFET drain circuit.

#### Step 9: Final Assembly

1. Mount Raspberry Pi 5 in enclosure using M2.5 hex standoffs.
2. Mount PCA9685 on standoffs or with adhesive.
3. Mount gimbal assembly in enclosure with laser aperture aligned.
4. Mount camera module behind enclosure window (clear acrylic).
5. Route all cables neatly; use zip ties.
6. Verify all interlock switches function.
7. Close enclosure and secure with screws.

### 6.3 Pre-Flight Checklist

Before powering on:

- [ ] All solder joints inspected (no cold joints, no bridges)
- [ ] Multimeter verification: 5V rail A = 5.0V ±0.1V, 5V rail B = 5.0V ±0.1V
- [ ] Continuity check: no short between 5V and GND
- [ ] MOSFET gate: 0V when GPIO18 LOW, 3.3V when GPIO18 HIGH
- [ ] Laser: OFF when GPIO18 LOW, ON when GPIO18 HIGH
- [ ] Servos: respond to PCA9685 commands
- [ ] Camera: detected by `libcamera-hello`
- [ ] Interlock: laser disabled when enclosure open
- [ ] E-Stop: laser disabled when pressed
- [ ] Status LEDs: correct color for state
- [ ] Battery: fully charged, BMS functional

---

## 7. 3D Printable Enclosure Design

### 7.1 Enclosure Overview

The enclosure consists of two main parts: a base housing that contains all electronics, and a top cover with the laser aperture. The design is optimized for FDM 3D printing with PLA or PETG.

```
┌──────────────────────────────────────────────────────────────────┐
│                    ENCLOSURE DIMENSIONS (mm)                      │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    TOP VIEW                              │    │
│  │                                                          │    │
│  │     ┌──────────────────────────────────────────┐         │    │
│  │     │                                          │         │    │
│  │     │    ┌──────┐              ┌──────┐        │         │    │
│  │     │    │Camera│              │Laser │        │         │    │
│  │     │    │Window│              │Apert.│        │         │    │
│  │     │    │ 25mm │              │ 15mm │        │         │    │
│  │     │    └──────┘              └──────┘        │         │    │
│  │     │                                          │         │    │
│  │     │         ┌──────────────┐                 │         │    │
│  │     │         │  IR LED      │                 │         │    │
│  │     │         │  (left)      │                 │         │    │
│  │     │         └──────────────┘                 │         │    │
│  │     │                                          │         │    │
│  │     │         ┌──────────────┐                 │         │    │
│  │     │         │  IR LED      │                 │         │    │
│  │     │         │  (right)     │                 │         │    │
│  │     │         └──────────────┘                 │         │    │
│  │     │                                          │         │    │
│  │     └──────────────────────────────────────────┘         │    │
│  │                        150mm                           │    │
│  └─────────────────────────────────────────────────────────┘    │
│                           100mm                                 │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   FRONT VIEW                             │    │
│  │                                                          │    │
│  │     ┌──────────────────────────────────────────┐         │    │
│  │     │  ┌────┐                        ┌────┐   │         │    │
│  │     │  │E- │                        │Stat│   │         │    │
│  │     │  │Stop│                        │LEDs│   │         │    │
│  │     │  └────┘                        └────┘   │         │    │
│  │     │                                          │         │    │
│  │     │         ┌──────────────┐                 │         │    │
│  │     │         │  ENCLOSURE   │                 │         │    │
│  │     │         │  BODY        │                 │         │    │
│  │     │         │              │                 │         │    │
│  │     │         └──────────────┘                 │         │    │
│  │     │                                          │         │    │
│  │     └──────────────────────────────────────────┘         │    │
│  │                                                          │    │
│  │     ◄────────────── 150mm ──────────────►               │    │
│  │                                                          │    │
│  │     Height: 80mm (body) + 20mm (lid) = 100mm total     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   SIDE VIEW                              │    │
│  │                                                          │    │
│  │     ┌──────────────────────────────────────────┐         │    │
│  │     │                                          │         │    │
│  │     │    ┌──────────────┐                      │         │    │
│  │     │    │  Cable       │                      │         │    │
│  │     │    │  Gland       │                      │         │    │
│  │     │    └──────────────┘                      │         │    │
│  │     │                                          │         │    │
│  │     │    ┌──────────────┐                      │         │    │
│  │     │    │  Vent        │                      │         │    │
│  │     │    │  (fan)       │                      │         │    │
│  │     │    └──────────────┘                      │         │    │
│  │     │                                          │         │    │
│  │     └──────────────────────────────────────────┘         │    │
│  │                                                          │    │
│  │     ◄────────────── 100mm ──────────────►               │    │
│  │                                                          │    │
│  │     Height: 100mm (total)                                │    │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### 7.2 Detailed Design Specifications

#### Base Housing

| Parameter | Value |
|-----------|-------|
| Material | PLA or PETG (matte black preferred) |
| Outer dimensions | 150mm (W) × 100mm (D) × 80mm (H) |
| Wall thickness | 3mm |
| Corner radius | 5mm |
| Mounting | 4× M3 rubber feet (bottom) |
| Internal standoffs | 6× M2.5 × 6mm hex standoffs (for Pi, PCA9685, prototype boards) |
| Ventilation | 2× 30mm fan vents (rear wall) + 20mm fan grille |
| Cable entry | 1× PG7 cable gland (for battery cable) |
| Microswitch mount | Internal clip for limit switch on lid lip |

#### Top Cover (Lid)

| Parameter | Value |
|-----------|-------|
| Material | PLA or PETG (matte black) |
| Outer dimensions | 150mm (W) × 100mm (D) × 20mm (H) |
| Wall thickness | 3mm |
| Laser aperture | 15mm diameter hole, centered at (110mm, 50mm) from front-left |
| Camera window | 30mm × 30mm cutout with 2mm thick clear acrylic insert |
| IR LED windows | 2× 20mm × 10mm cutouts on either side of camera |
| Interlock switch | Microswitch mounted on inner surface, actuated by base tab |
| Lid latch | 2× snap-fit clips or 2× M3 thumbscrews |

#### Gimbal Mount Plate

| Parameter | Value |
|-----------|-------|
| Material | Aluminum (1.5mm) or PETG |
| Dimensions | 80mm (W) × 60mm (D) |
| Servo X mount | 4× M3 holes at standard servo mounting pattern (40mm × 20mm) |
| Servo Y mount | 4× M3 holes at standard servo mounting pattern (40mm × 20mm) |
| Laser mount | 12mm diameter bore for brass laser housing |
| Bearing point | 6mm bore for tilt axis bearing |

### 7.3 3D Model Files (Descriptions)

> **Note:** Actual .STL/.STEP files should be created in CAD software (Fusion 360, FreeCAD, OpenSCAD). Below are the design descriptions for parametric modeling.

#### File: `enclosure_base.scad` (OpenSCAD)

```openscad
// Base Housing
$fn = 60;

difference() {
    // Outer shell
    rounded_cube(150, 100, 80, radius=5);
    
    // Inner cavity
    translate([3, 3, 3])
        rounded_cube(144, 94, 77, radius=3);
    
    // Cable gland hole (rear wall)
    translate([75, 0, 40])
        cylinder(h=10, d=12);
    
    // Fan vents (rear wall)
    for(x=[45, 105]) {
        translate([x, 0, 40])
            cylinder(h=10, d=30);
    }
    
    // Vent slots
    for(i=[0:9]) {
        translate([50+i*8, 0, 20])
            cylinder(h=10, d=3);
    }
}

// Internal standoffs
for(pos=[[10,10],[10,90],[140,10],[140,90],[75,10],[75,90]]) {
    translate([pos[0], pos[1], 3])
        cylinder(h=8, d=5);
}
```

#### File: `enclosure_lid.scad` (OpenSCAD)

```openscad
// Top Cover
$fn = 60;

difference() {
    // Outer shell
    rounded_cube(150, 100, 20, radius=5);
    
    // Inner cavity
    translate([3, 3, 3])
        rounded_cube(144, 94, 17, radius=3);
    
    // Laser aperture (15mm)
    translate([110, 50, 0])
        cylinder(h=25, d=15);
    
    // Camera window (30x30mm)
    translate([35, 35, 0])
        cube([30, 30, 25]);
    
    // IR LED windows (20x10mm each)
    translate([20, 35, 0])
        cube([20, 10, 25]);
    translate([60, 35, 0])
        cube([20, 10, 25]);
    
    // Status LED holes (5mm each)
    translate([135, 20, 0])
        cylinder(h=25, d=5);
    translate([135, 35, 0])
        cylinder(h=25, d=5);
}
```

#### File: `gimbal_mount.scad` (OpenSCAD)

```openscad
// Gimbal Mount Plate
$fn = 60;

difference() {
    // Base plate
    cube([80, 60, 3]);
    
    // Servo X mounting holes (standard 40x20mm pattern)
    for(pos=[[10,10],[10,30],[50,10],[50,30]]) {
        translate([pos[0], pos[1], 0])
            cylinder(h=5, d=3.5);
    }
    
    // Servo Y mounting holes
    for(pos=[[10,10],[10,30],[50,10],[50,30]]) {
        translate([pos[0]+20, pos[1]+20, 0])
            cylinder(h=5, d=3.5);
    }
    
    // Laser bore (12mm)
    translate([40, 30, 0])
        cylinder(h=5, d=12);
    
    // Tilt axis bore (6mm)
    translate([40, 0, 0])
        cylinder(h=5, d=6);
}
```

### 7.4 Print Settings

| Parameter | Value |
|-----------|-------|
| Layer height | 0.2mm |
| Wall thickness | 1.2mm (3 perimeters) |
| Infill | 20% gyroid |
| Supports | Yes (for overhangs on lid aperture) |
| Print orientation | Base: bottom down; Lid: inner surface down |
| Material | PLA (matte black) or PETG |
| Estimated print time | Base: ~4 hours; Lid: ~2 hours; Gimbal: ~1 hour |
| Estimated filament | Base: ~180g; Lid: ~60g; Gimbal: ~30g |

---

## 8. Firmware & Software Interface

### 8.1 Software Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| OS | Raspberry Pi OS Lite (64-bit) | Base operating system |
| Camera | libcamera / Picamera2 | Image capture |
| Vision | OpenCV 4.x + NumPy | Frame processing, contour detection |
| AI (optional) | TensorFlow Lite / ONNX Runtime | Mosquito classification |
| Servo Control | Adafruit Circuitpython Servokit | PCA9685 servo PWM |
| Laser Control | RPi.GPIO | GPIO18 → MOSFET gate |
| Web UI | Flask + SocketIO | Monitoring & manual control |
| Config | YAML | Runtime parameters |

### 8.2 GPIO Software Interface

```python
# Hardware abstraction layer (GPIO map)

PIN_MAP = {
    'laser_enable': 18,      # Pin 12 — MOSFET gate
    'servo_pan_pwm': 'PCA9685_0',   # Channel 0
    'servo_tilt_pwm': 'PCA9685_1',  # Channel 1
    'ir_led': 22,            # Pin 15 — via MOSFET (optional PWM dimming)
    'status_red': 22,        # Pin 15 — Red LED
    'status_green': 23,      # Pin 16 — Green LED
    'interlock_enclosure': 27,  # Pin 13 — Enclosure closed switch
    'emergency_stop': 17,    # Pin 11 — E-Stop button
    'proximity_sensor': 23,  # Pin 16 — PIR/ultrasonic (optional)
    'i2c_sda': 2,            # Pin 3 — PCA9685
    'i2c_scl': 3,            # Pin 5 — PCA9685
}

# I2C Addresses
I2C_MAP = {
    'pca9685': 0x40,  # Servo driver (default address)
    'oled': 0x3C,     # Optional status display
}

# PWM Configuration
PWM_CONFIG = {
    'servo_freq': 50,        # 50Hz for servos
    'servo_min_pulse': 150,  # 0.6ms → 0 degrees
    'servo_max_pulse': 600,  # 2.4ms → 180 degrees
    'laser_pwm_freq': 1000,  # 1kHz for laser MOSFET (if PWM dimming)
}
```

### 8.3 Safety State Machine

```
┌──────────────────────────────────────────────────────────────┐
│                    SAFETY STATE MACHINE                       │
│                                                              │
│   ┌─────────┐                                                │
│   │  INIT   │ → Check all GPIOs, I2C, camera               │
│   └────┬────┘                                                │
│        │ All OK                                              │
│        ▼                                                     │
│   ┌─────────┐                                                │
│   │  IDLE   │ → Green LED on, laser OFF, servos centered    │
│   └────┬────┘                                                │
│        │ Interlock OK + E-Stop released                     │
│        ▼                                                     │
│   ┌─────────┐                                                │
│   │  ARMED  │ → Green LED blink, camera active, tracking    │
│   └────┬────┘                                                │
│        │ Target locked + fire command                        │
│        ▼                                                     │
│   ┌─────────┐                                                │
│   │  FIRE   │ → Laser ON (max 500ms pulse), track result    │
│   └────┬────┘                                                │
│        │ Pulse complete or target lost                       │
│        ▼                                                     │
│   ┌─────────┐                                                │
│   │ COOLDOWN│ → 500ms wait, then return to ARMED           │
│   └─────────┘                                                │
│                                                              │
│   Emergency transitions (from ANY state):                   │
│   - Interlock open → IDLE (laser OFF, servos center)        │
│   - E-Stop pressed → IDLE (laser OFF, require reset)        │
│   - Over-temperature → IDLE (require cooldown)             │
│   - Proximity alert → IDLE (require clear zone)            │
└──────────────────────────────────────────────────────────────┘
```

---

## 9. Testing & Validation

### 9.1 Bench Tests

| Test | Procedure | Expected Result |
|------|-----------|-----------------|
| Power-on | Connect battery, power on | Pi boots, green LED on, servos center |
| I2C scan | `i2cdetect -y 1` | PCA9685 at 0x40 |
| Camera test | `libcamera-hello` | Preview window opens |
| Servo sweep | Run sweep script | Both servos 0°→180°→0° |
| Laser toggle | GPIO18 HIGH/LOW | Laser ON/OFF |
| Interlock test | Open enclosure lid | Laser disabled, red LED on |
| E-Stop test | Press E-Stop | Laser power cut, red LED on |
| Temperature | `vcgencmd measure_temp` | <70°C under load |

### 9.2 Field Tests

| Test | Procedure | Expected Result |
|------|-----------|-----------------|
| Detection range | Place mosquito targets at 0.5m, 1m, 2m, 3m | Detection within 2s at all ranges |
| Tracking accuracy | Move target in figure-8 pattern | Laser tracks within 5mm |
| Kill rate | 10 mosquito targets | >70% neutralized |
| Battery runtime | Continuous operation until shutdown | >2 hours |
| Interlock response | Open lid during operation | Laser off in <100ms |
| Weather resistance | Light rain (outdoor use) | No short circuits |

---

## 10. Regulatory Compliance

### 10.1 Laser Regulations

| Regulation | Classification | Requirement |
|------------|---------------|-------------|
| IEC 60825-1 | Class 3R (5mW) | Safety interlocks, warning labels |
| IEC 60825-1 | Class 3B (100mW) | Key switch, emission indicator, interlocks |
| FDA 21 CFR 1040.10 | Class IIIb | Product report, variance for outdoor use |
| CDRH | All classes | Performance standards compliance |

### 10.2 CE / FCC Compliance

- **EMC Directive 2014/30/EU:** System must not emit excessive electromagnetic interference.
- **RoHS Directive 2011/65/EU:** All components must be RoHS compliant.
- **RED Directive 2014/53/EU:** If using Wi-Fi/Bluetooth modules, must comply with radio equipment directive.

### 10.3 Liability Disclaimer

> **⚠️ DISCLAIMER:** This device is designed for educational and research purposes only. The builder assumes all responsibility for safe operation. Laser safety regulations vary by jurisdiction. Check local laws before operating. The author(s) accept no liability for injury, damage, or legal consequences resulting from use of this device. **Never point the laser at people, animals, aircraft, or vehicles.** Always wear appropriate laser safety glasses when testing with the enclosure open.

---

## Appendix A: Component Supplier Quick Reference

| Component | Primary Supplier | Backup Supplier | Part Number |
|-----------|-----------------|-----------------|-------------|
| Raspberry Pi 5 4GB | raspberrypi.com | CanaKit, Adafruit | SC1112 |
| microSD 64GB | Amazon | SanDisk | SDSQXCG-064G |
| Pi 27W PSU | raspberrypi.com | Waveshare | SC1160 |
| Active Cooler | raspberrypi.com | Amazon | SC1186 |
| IMX219 Camera | Arducam | Amazon | B0275 |
| Wide-Angle Lens | Amazon | — | — |
| 650nm 5mW Laser | Accio | AliExpress | — |
| 650nm 100mW Laser | Accio | AliExpress | — |
| MG996R Servo | Amazon | AliExpress, Adafruit | — |
| PCA9685 | Adafruit | ElectroPeak | 815 |
| 850nm IR LED | Amazon | AliExpress | — |
| 18650 Battery | Amazon | Panasonic | NCR18650B |
| LM2596 | Amazon | AliExpress | LM2596S |
| XL4015 | Amazon | AliExpress | XL4015 |
| IRLZ44N | Amazon | DigiKey | IRLZ44NPBF |
| 1N4007 | DigiKey | Amazon | 1N4007 |
| 1000µF 25V Cap | SparkFun | Newark | COM-08982 |
| Microswitch | DigiKey | Omron | V-15-1C25 |

---

## Appendix B: Cost Optimization Tips

1. **Substitute Jetson Orin Nano** ($249, 67 TOPS) for Pi 5 if AI inference speed is critical — the CUDA cores accelerate detection by 5-10×.
2. **Buy components from AliExpress** for lowest cost (2-4 week shipping).
3. **Use 5mW laser** for Class 3R compliance (fewer regulatory requirements).
4. **Skip 3D printing** — use a project enclosure box ($12) for prototype.
5. **Use USB power bank** instead of 18650 pack for initial testing.
6. **Raspberry Pi 5 price note:** 4GB model currently $110 (up from $60 at launch) due to LPDDR4 DRAM shortages. The Pi 4 4GB ($100) or Pi 5 2GB ($77.50) are budget alternatives with sufficient performance for this application.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-10-11 | — | Initial release |

---

*End of specification document.*

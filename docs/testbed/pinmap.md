# Common node board pin map: established facts and open points

- Written: 2026-11-20 (a phase 1 specification-freeze preparation draft)
- Surveyed: `firmware/` (each app's `boards/nucleo_h753zi.overlay`, `crosslink.overlay`, `prj.conf`, extra conf files, `src/main.c`, common code), the `Makefile` (`BOARD ?= nucleo_h753zi`), `firmware/common/identity.cmake`, and the relevant parts of the design document `docs/superpowers/specs/2026-08-28-cuberange-design.md`
- Principle: only what a file in this repository supports is listed as an established fact. Zephyr's board definitions (`nucleo_h753zi.dts` and friends) live outside this repository, in the toolchain, so anything inherited from them is marked "not confirmed in this repository". Points that general knowledge could fill in are likewise not treated as established without a basis in the repository.

## 1. Established facts (with a basis in this repository)

### 1.1 Platform assumptions

| Item | Fact | Basis |
|---|---|---|
| Board / MCU | The Zephyr board `nucleo_h753zi` (STM32H753). All firmware builds for this board | `Makefile`: `BOARD ?= nucleo_h753zi`; each mitigation.md's `west build -b nucleo_h753zi` |
| Emulation model | Renode uses `platforms/boards/nucleo_h753zi.repl` (its contents reuse the STM32H743 model; the overlays' comments quote its gpioPortB `invertedAFPins` and similar) | Each `scenario.resc`; the overlay comments |
| Node roles and CSP addresses | Spacecraft 0: OBC=1, EPS=2, ADCS=4, COMM=5; the crosslink terminal is 6 (base+6); SCID/APID=0x0A9. +8 per spacecraft index (up to 4 spacecraft). Ground station source ids: primary=66 (0x0042), backup=67 (0x0043) | `firmware/common/identity.cmake` |
| Peripherals in use | Across all apps' `prj.conf`, what is enabled is LIBCSP, CAN, SERIAL/UART (COMM only), GPIO (EPS/ADCS/OBC only), events, and the heap. **No conf in this repository enables ADC, I2C, SPI, USB, or Ethernet** | `firmware/apps/*/prj.conf`, `sdls.conf`, `pus_auth.conf`, `crosslink.conf` |

### 1.2 CAN (FDCAN1: the internal bus)

| Item | Fact | Basis |
|---|---|---|
| Controller | FDCAN1. Every app obtains it via `DT_CHOSEN(zephyr_canbus)` (the board definition declares `zephyr,canbus = &fdcan1`, as the csp_ping comment states) | `firmware/apps/*/src/main.c`; the `csp_ping/src/main.c` comment |
| Bit rate | **1,000,000 bps** (the `CAN_BITRATE` constant common to all apps; adcs/eps spell it 1000000, the others the same value) | Each `src/main.c` |
| CAN FD | **Not used (CAN classic)**. `CONFIG_CAN_FD_MODE` is off and `CAN_MAX_DLEN` is 8, as the crosslink conf's comment states | The `firmware/apps/comm/crosslink.conf` comment |
| FDCAN1 TX/RX pins | **Not specified in any overlay in this repository** (no file touches the fdcan1 node). The pins are inherited from the Zephyr board definition (the Nucleo-H753ZI dts) | The absence of any fdcan1 pinctrl description across the overlays (the absence is the basis) |
| Kernel clock | The board definition's &fdcan1 node adds a KERNEL clock source (PLL2_Q) — confirmed indirectly: the crosslink overlay states it "copied the clocks of the board's &fdcan1" for fdcan2 | The `crosslink.overlay` comment |
| Transceiver | The Nucleo-144 carries one CAN transceiver and fdcan1 uses it, as the overlay states. **Nothing in this repository discusses transceiver selection for a custom board** | The `crosslink.overlay` comment |

### 1.3 CAN (FDCAN2: the crosslink, in crosslink builds only)

| Item | Fact | Basis |
|---|---|---|
| Where enabled | Only in crosslink builds, applied additionally via `EXTRA_DTC_OVERLAY_FILE`. It is not in a normal COMM build (to avoid breaking the vuln/hard pair's Kconfig comparison, as stated) | The `crosslink.overlay` opening comment; the `Makefile` firmware-* targets |
| Pins | **PB12 = RX, PB13 = TX** (stated as this package's FDCAN2 pinout) | `crosslink.overlay`: `pinctrl-0 = <&fdcan2_rx_pb12 &fdcan2_tx_pb13>` |
| Conflict | PB13 is shared with Ethernet TXD1; on the real Nucleo this is resolved by jumpers JP6/JP7 (quoting the board definition's warning). COMM uses no networking, so no mac driver claims the pin, as stated | The `crosslink.overlay` comment |
| Clocks | The `clocks` property needs two entries: the APB1_2 bus clock and the KERNEL source PLL2_Q (`STM32_SRC_PLL2_Q FDCAN_SEL(2)`). Without them there is no reference from which to derive the bit rate | The `crosslink.overlay` body and comment |
| Message RAM | Shared with fdcan1 (0x4000AC00); fdcan2's section is at `bosch,mram-cfg` offset 0x350 | The `crosslink.overlay` comment |
| Bit rate | The same 1 Mbps as FDCAN1 (`CAN_BITRATE` is also used for the XLINK interface) | `comm/src/main.c` (the XLINK-side call of csp_can_open_and_add_interface) |
| A Renode caveat | Renode does not model pin multiplexing, so this pin choice is irrelevant to behaviour under Renode. It was chosen so as not to "write a devicetree that would not run on real hardware and make it look factual", as stated | The `crosslink.overlay` comment |

### 1.4 UART

| Item | Fact | Basis |
|---|---|---|
| Space link | **USART2, PA2 = TX / PA3 = RX, 115200 bps**. It is `status = "disabled"` in the SoC dtsi, so the overlay enables it. Alias `spacelink` | `comm/boards/nucleo_h753zi.overlay`, `csp_ping/boards/nucleo_h753zi.overlay` (identical content) |
| Console | **USART3** is `zephyr,console` and `zephyr,shell-uart` (a board-definition setting, as the overlay comment states). The reason for separating it from the link: shared, the ground station would receive 9239 bytes of boot banner before the first application byte | The same overlay comment; the design document says the same |
| USART3 pins | **Not specified in this repository** (inherited from the board definition. Whether it is the Nucleo's ST-LINK VCP connection also has no basis in this repository) | No usart3 pinctrl description in any overlay |
| Logging | `CONFIG_LOG=n` (all apps). The console carries only the firmware's own printk. Nothing but the link protocol may flow on USART2 — an invariant | Each `prj.conf` and its comments |

### 1.5 GPIO (role indication)

| Signal | Pin | Polarity / binding | Basis |
|---|---|---|---|
| EPS: COMM rail (load-switch indication) | **PD5** | `GPIO_ACTIVE_HIGH`, `gpio-leds` binding, alias `commrail` | `eps/boards/nucleo_h753zi.overlay` |
| ADCS: tumble indication | **PD6** | The same, alias `tumbleflag` | `adcs/boards/nucleo_h753zi.overlay` |
| OBC: FDIR inhibit indication | **PD7** | The same, alias `fdirinhibit` | `obc/boards/nucleo_h753zi.overlay` |
| Why port D | In Renode's stm32h743 model, several port B pins (PB0, PB1, PB6, PB7, PB13, PB14, PB15) are in `invertedAFPins` and read inverted against the ODR once an alternate function is configured. Port D has no such entries. **This is a property of the Renode model, not an electrical constraint of the real part** (the overlay comments themselves cite Renode's `.repl` as the basis) | Each overlay's comment |
| Initial values | The EPS rail starts ON (`GPIO_OUTPUT_ACTIVE`); the ADCS/OBC indications start INACTIVE | Each `src/main.c` |

### 1.6 Other constraints readable from the firmware

| Item | Fact | Basis |
|---|---|---|
| Cryptography | SDLS / PUS authentication is mbedTLS (AES-256-GCM, a software implementation). The H7's CRYP hardware accelerator is not used (design 6.5: the CRYP is an accelerator, not a vault, and keys remain in firmware-managed memory — as the EX-F02 document also states) | `sdls.conf`, `pus_auth.conf`, the EX-F02 README |
| Stack | Crypto builds need `CONFIG_MAIN_STACK_SIZE=3072` plus `CONFIG_HW_STACK_PROTECTION=y` as near-mandatory (a measured stack overflow from the 424-byte GCM context is recorded) | The `sdls.conf` and `pus_auth.conf` comments |
| Heap | `CONFIG_HEAP_MEM_POOL_SIZE=8192` for the crosslink (two CAN interfaces), 4096 normally | `crosslink.conf`, `prj.conf` |
| Key placement | The SDLS key is `static const` (`firmware/common/cuberange_keys.h`) — resident in flash `.rodata`. The design uses no secure element | `cuberange_keys.h`, the EX-F02 README |

## 2. Open points (what fixing the common node board's pin map requires)

### A. Around CAN

1. **FDCAN1 TX/RX pin selection**: the current firmware inherits the board definition, and no pin assignment exists in this repository. A custom board must fix a choice from the candidate pins (selected against the package's AF table), in a combination that does not conflict with putting FDCAN2 on PB12/PB13 (section 1.3). Whether to add a custom-board overlay with explicit pinctrl on the firmware side must also be decided.
2. **Transceiver selection and its control pins**: the part number (3.3 V or 5 V family, how VIO is handled), whether standby/silent control pins go to MCU GPIOs or are tied off, and whether silent mode is used as fault injection (a way to silence the bus, for exercises). The current firmware has no concept of transceiver control.
3. **How termination is implemented**: the roadmap decision is "trunk ends only; switching is separated out as fault injection". Whether termination lives on the node boards (DIP / jumper / unpopulated pads) or is kept on the backplane is undecided — including the 120 Ω implementation form and how to prevent the accident of every node terminating at once.
4. **Whether the crosslink (FDCAN2) is in the v1 board**: four exercises in the compatibility table (X01/S01/S02/D01) depend on it. If included: a second transceiver; the PB13/Ethernet sharing problem disappears on a custom board that carries no Ethernet, but **PB13 is one of the Renode model's invertedAFPins pins**, so mapping between the real pin map and Renode verification needs care. If not included, the specification freeze document must state plainly that the four are deferred.
5. **Fixing the CAN clock**: the FDCAN kernel clock is PLL2_Q (board-definition inheritance plus explicit in crosslink.overlay). What frequency PLL2_Q runs at in a custom board's clock tree, and whether the target sample point at 1 Mbps is left to Zephyr's default derivation or stated explicitly, is undecided. The bit rate itself (1 Mbps) is fixed by the firmware and settled.

### B. Clocks and crystals

6. **System clock source and crystal**: this repository contains **no specification at all** of the HSE frequency or even its presence (the Nucleo's ST-LINK MCO bypass versus a crystal), nor of the SYSCLK frequency — all inherited from the board definition. A custom board must fix the crystal frequency and load capacitance, whether an MCO input is taken, and the PLL settings (the source of SYSCLK and of PLL2_Q). Until this is decided, point A-5 cannot be fixed either.
7. **Whether an LSE (32.768 kHz) is needed**: no RTC or low-power use exists in the current firmware. Whether to fit one is undecided (it affects the parts count and future time-related exercises).

### C. Console, USB, debug

8. **The console's (USART3) physical interface**: the roadmap's target shape includes a "USB-C console", but the current firmware has no USB device function. The options — (a) fit a USB-UART bridge IC connected to USART3, (b) implement CDC on the STM32H753's native USB (OTG_FS), which needs firmware work, (c) USB for power only and the console as a pin-header UART — are undecided. So is fixing USART3's TX/RX pins themselves (currently board-inherited and unspecified).
9. **The space link's (USART2) on-board interface**: PA2/PA3 are established facts. How they leave the board (over the backplane, or a connector only on the COMM node), the connection form to the GS bridge (whether a 3.3 V TTL level assumption is acceptable), and whether USART2 is also routed to nodes other than COMM (only COMM and csp_ping use USART2 in the current firmware) are undecided.
10. **How SWD is brought out**: this repository contains **no mention of SWD at all**. The connector form (Tag-Connect / 10-pin Cortex Debug / pin header), SWDIO/SWCLK (PA13/PA14 are fixed by the package, but there is no basis in this repository — to be treated as requiring a datasheet check), whether SWO is brought out, and whether the four nodes' SWD can be aggregated over the backplane (or whether per-node is the sound choice for signal quality) are undecided. This is one of the most important points, because it is every exercise's image-switching means.
11. **External control of NRST/BOOT**: included in the roadmap's design requirements (certain return to the initial state after an exercise), but with no concrete form on the firmware or repository side. Bringing the BOOT0 pin out (and whether the DFU bootloader becomes a reserve reflash path), whether NRST can be driven from the backplane or a control node, and the control GPIOs' assignment are undecided.

### D. Power and measurement

12. **The EPS rail signal (PD5) versus a real load switch**: the current firmware's PD5 is an "indication GPIO"; under Renode a host process simulated the power cut on its behalf. On a custom board, whether PD5 drives a load-switch IC's EN directly, whether a different GPIO is assigned, and whether the switch sits on the EPS node board or on the backplane are undecided. Keeping PD5 would make it a real switch with no firmware change (the leading candidate, but not decided).
13. **Who controls the per-node power switches**: the roadmap makes "per-node power switch plus current measurement" a common-board function, but who controls the switch (an EPS firmware GPIO, a control circuit in the backplane's always-on domain, a physical switch alongside) is undecided — including who can cut the EPS's own power.
14. **The current-measurement method and ADC pins**: the current firmware uses no ADC at all. A measurement IC (an I2C-output current monitor, say) and a shunt plus MCU ADC need different pins. The latter also raises ADC channel assignment and getting values into the firmware (the current protocol has no field for a current value — whether firmware changes are needed). Using I2C would also require newly fixing I2C bus pins (currently unused).
15. **Power input and regulation**: the EPS single-point entry is USB-C 5 V (the roadmap). The pin-fixing questions that come with the power architecture — 3.3 V generation on the node board (LDO / DC-DC choice), the STM32H753's internal regulator mode (the LDO/SMPS choice affects pin strapping), the handling of the VCAP pins — are undecided, as points outside this repository.
16. **The role-indication GPIOs (PD5/PD6/PD7)**: the reason for port D is a property of the Renode model, not a real-hardware constraint. Keeping PD5/6/7 on a custom board needs no firmware change, but in a design where one board serves every role, the form becomes "indication LEDs fitted to every board, their meaning decided by the role firmware". How the LEDs are fitted (all three on every board) and the interaction with PD5 doubling as the switch control (point 12) need sorting out.

### E. Role I/O and other

17. **The scope of role-specific I/O**: the roadmap makes "role-specific I/O" the common board's differentiating element, but the only role-specific signals the current firmware requires are the three indication GPIOs. How far v1 goes on the board with real ADCS actuators (magnetorquer drive circuits and the like) or extra EPS rails is undecided (in the compatibility table, A01 and B01 both stand with indication-level equivalents).
18. **Unused-pin handling and board test points**: the policy for floating pins and for test points for bring-up (the minimum set: power, SWD, UART, CAN) is undecided.
19. **Board identification (role / unit)**: one board design is used four times, so the role and the spacecraft index are decided by build-time constants (identity.cmake). Whether to give the board ID strap pins or a DIP switch that the firmware reads is undecided.

## 3. A note on completeness

The survey behind this draft identified twenty open points, but the text of the twentieth, and the end of the nineteenth, were lost to a truncated write before review. They are recorded here as missing rather than reconstructed; re-deriving them is phase 1 work.

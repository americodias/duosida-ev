# Duosida Charger Settings Reference

This document explains the configuration settings available on Duosida EV chargers.

## Direct Work Mode (VendorDirectWorkMode)

**Direct Work Mode**, also known as "Plug and charge mode" or "Plug Then Charge Mode," controls whether your charger requires authentication before starting a charging session.

### When Enabled

- The charger operates in automatic mode—simply plug in your EV and charging begins immediately
- No RFID card tap or app activation is required
- The charger bypasses authentication, making the process more convenient for private home installations

### When Disabled

- You must authenticate each charging session using either an RFID card or the mobile app before power flows to your vehicle
- This provides access control, preventing unauthorized users from charging at your station
- The charger enters a "Preparing" state after plug insertion and waits up to 120 seconds for authentication before timing out

### Recommendations

- **Enable** if you're using the charger at home in a private location where only authorized users have physical access
- **Disable** if installed in a semi-public area (shared parking, workplace) or if you want to track individual charging sessions via RFID cards

**Note:** When using IC card management mode with prepaid kWh credits, Direct Work Mode cannot be enabled—you must use card-based authentication to properly track and deduct energy usage.

## Level Detection (CheckCpN12V)

**CheckCpN12V** refers to Control Pilot Negative 12V detection, a critical safety verification feature in EV charging.

### How It Works

The Control Pilot (CP) line is the primary communication channel between your charger and the electric vehicle. According to IEC 61851-1 and SAE J1772 standards, the CP signal operates as a 1 kHz square wave oscillating between +12V and -12V.

The negative voltage phase serves multiple critical functions:

#### 1. Connection Integrity Verification

The -12V portion of the signal (created by a diode in the vehicle) confirms a proper electrical connection exists between the charger and vehicle. If the negative voltage isn't detected correctly, it indicates a wiring fault or improper connection.

#### 2. Safety State Detection

The CP signal communicates different charging states based on voltage levels:

| Voltage | State | Description |
|---------|-------|-------------|
| +12V / -12V | State A | No vehicle connected |
| +9V / -12V | State B | Vehicle connected, not ready to charge |
| +6V / -12V | State C | Vehicle connected and charging |
| +3V / -12V | State D | Charging with ventilation required |
| -12V only | State F | Fault condition |

#### 3. Fault Protection

The diode in the vehicle that creates the negative voltage drop acts as a safety mechanism. If the charging connector falls into water or experiences a short circuit, both positive and negative voltages would appear on the CP pin, triggering an immediate shutdown.

### When Enabled

- The charger actively monitors and verifies that the negative portion of the CP signal is present and within the correct range (-12V ±0.4V)
- Provides enhanced fault detection, ensuring the vehicle's electrical systems are properly responding
- If the -12V signal is out of range or missing, the charger will abort charging and display an error

### When Disabled

- The charger may not strictly verify the negative voltage level, potentially reducing sensitivity to certain connection faults
- This might be used to accommodate vehicles or cables with non-standard CP implementations, though this compromises safety

### Recommendations

- **Keep enabled** unless specifically instructed otherwise by technical support
- This safety feature protects both your vehicle and charger by detecting abnormal electrical conditions
- Only disable if you're experiencing compatibility issues with a specific vehicle model and have confirmed this with the manufacturer

## References

- [Duosida WiFi Manual](https://evchargers.com.pt/wp-content/uploads/2021/04/Manual-Wallbox-Duosida-Wi-Fi.pdf)
- [Duosida Instruction Manual](https://www.pluganddrive.uk/app/uploads/2022/03/Duosida-Instruction-Manual-small.pdf)
- [IEC 61851-1 EV Charging Standard](https://www.einfochips.com/blog/iec-61851-everything-you-need-to-know-about-the-ev-charging-standard/)
- [Type 2 AC EV Charging - CP Signal](https://wiki.morek.eu/en/support/solutions/articles/204000046254-about-type-2-ac-ev-charging)
- [Weidmuller - Checking the CP Contact](https://www.weidmueller.com/int/solutions/industries/e_mobility/e_mobility_service/e_mobility_service_knowledge_platform/e_mobility_service_checking_the_cp_contact.jsp)

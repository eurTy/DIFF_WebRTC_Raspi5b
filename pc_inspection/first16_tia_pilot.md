# TIA V21 first-16-input pilot

This PC-only pilot diagnoses station-A inputs. `I0.0`-`I1.7` are **planned**
physical addresses, not installed hardware. In PLCSIM, use editable DB bits
first. This FC has no Q outputs and is not a safety function.

## Signal wiring

Create `DB_AInputSim` with these 16 Bool members. In OB1, wire each member to
the identically named input of `FC_AInputDiagnostics`.

| Planned address | Tag in 100-point plan | DB/FC member |
| --- | --- | --- |
| I0.0 | A_DI_infeed_photoeye | InfeedPhotoeye |
| I0.1 | A_DI_part_present | PartPresent |
| I0.2 | A_DI_magazine_low | MagazineLow |
| I0.3 | A_DI_clamp_extended | ClampExtended |
| I0.4 | A_DI_clamp_retracted | ClampRetracted |
| I0.5 | A_DI_pusher_extended | PusherExtended |
| I0.6 | A_DI_pusher_retracted | PusherRetracted |
| I0.7 | A_DI_lift_up | LiftUp |
| I1.0 | A_DI_lift_down | LiftDown |
| I1.1 | A_DI_vacuum_ok | VacuumOk |
| I1.2 | A_DI_motor_ready | MotorReady |
| I1.3 | A_DI_jam_detected | JamDetected |
| I1.4 | CELL_DI_start_button | StartButton |
| I1.5 | CELL_DI_stop_button | StopButton |
| I1.6 | CELL_DI_reset_button | ResetButton |
| I1.7 | CELL_DI_guard_door_closed | GuardDoorClosed |

Create `DB_AStatusSim` with `SensorConflict`, `HomePositionOk`, `FeedReady`,
`PickReady`, `StartRequestAccepted`, `ResetRequestObserved`, and
`PartAtInfeed` as Bool, plus `AlarmCode` as Int. Wire the eight FC outputs
to these members. `FeedReady` and `StartRequestAccepted` are advisory only;
`ResetRequestObserved` never clears a fault.

## First TIA session

1. Open TIA Portal V21 and create a **new local project** called
   `SVT_Assembly_Pilot`. For this pilot, first evaluate a standard
   S7-1200 G2 CPU 1214C (not the safety 1214FC). Confirm the exact catalog
   item, firmware, license, and PLCSIM compatibility before treating this
   as the final CPU choice. An S7-1500 CPU 1511-1 PN is a comparison option,
   not a requirement for 100 planned I/O points.
2. Create the two global DBs above under Program blocks. Keep them
   non-retentive and initialize pilot inputs FALSE.
3. Under **External source files**, add `FC_AInputDiagnostics.scl`; choose
   **Generate blocks from source**, then compile. Read errors in the TIA
   message pane. Do not overwrite a same-name block in another project.
4. Call the FC once from OB1, wire DB members by name, and compile the CPU.
   Start PLCSIM, download the CPU, put it in RUN, then use a watch/simulation
   table to modify only `DB_AInputSim` and monitor `DB_AStatusSim`.
5. Save the `.ap21` project. Record CPU model/firmware, compilation result,
   and a watch-table screenshot. Do not connect real outputs.

For later tag imports, export an actual TIA PLC tag table first and follow
that XLSX/SDF/XML schema. `io_map_100.csv` is **not** directly importable;
TIA V21's XLSX format uses `PLCTags` and `TagTableProperties` sheets.
The planned absolute I/Q addresses may change when the selected CPU's
onboard I/O and actual modules are configured; keep this pilot wired to
DB members until a device configuration has been compiled and verified.

## Acceptance cases

Reset all inputs FALSE before each case, then set `ClampRetracted`,
`PusherRetracted`, `LiftDown`, `MotorReady`, `GuardDoorClosed` TRUE. Baseline
must show `AlarmCode=0`, `FeedReady=TRUE`, `PickReady=FALSE`, and
`StartRequestAccepted=FALSE`.

| Extra change from baseline | Expected result |
| --- | --- |
| StartButton=TRUE | StartRequestAccepted=TRUE; AlarmCode=0 |
| PartPresent=TRUE; VacuumOk=TRUE | PickReady=TRUE |
| VacuumOk=TRUE only | PickReady=FALSE; FeedReady=TRUE |
| ClampExtended=TRUE | SensorConflict=TRUE; FeedReady=FALSE; AlarmCode=10 |
| GuardDoorClosed=FALSE | FeedReady=FALSE; AlarmCode=20 |
| StopButton=TRUE | FeedReady=FALSE; AlarmCode=30 |
| JamDetected=TRUE | FeedReady=FALSE; AlarmCode=40 |
| MotorReady=FALSE | FeedReady=FALSE; AlarmCode=50 |
| MagazineLow=TRUE | FeedReady=FALSE; AlarmCode=60 |
| LiftDown=FALSE | HomePositionOk=FALSE; FeedReady=FALSE; AlarmCode=70 |
| JamDetected=TRUE; ResetButton=TRUE | ResetRequestObserved=TRUE; FeedReady=FALSE; AlarmCode=40 |

The alarm code reports the first matching priority, not every fault.
`pilot_inputs.py` is a Python reference model; passing its tests does not
prove SCL compilation or PLCSIM execution. Full safety interlocks, timed
sequences, stations B/C, analog scaling, vision freshness, and the remaining
84 points are **not implemented** in this pilot. They follow only after a
successful TIA/PLCSIM check. No Pi video service or port is changed.

Official Siemens references: [SCL source generation](https://docs.tia.siemens.cloud/r/en-us/v21/creating-and-managing-blocks/using-external-source-files-for-stl-and-scl/generating-blocks-from-external-source-files), [PLC tag import formats](https://docs.tia.siemens.cloud/r/en-us/v21/declaring-plc-tags/editing-plc-tag-tables/exporting-and-importing-plc-tags/basics-for-exporting-and-importing-plc-tags), [XLSX structure](https://docs.tia.siemens.cloud/r/en-us/v21/declaring-plc-tags/editing-plc-tag-tables/exporting-and-importing-plc-tags/format-of-the-export-file-.xlsx).

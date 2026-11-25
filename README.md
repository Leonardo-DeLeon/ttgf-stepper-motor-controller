# Programmable Stepper Motor Controller

Controlador digital Verilog-2001 para un 28BYJ-48 con ULN2003 externo. Proyecto IEEE Open Silicon Bootcamp 2026, preparado para Tiny Tapeout GF180 TTGF26c. Usa un tile 1×1 y reloj externo de 1 MHz. El mando, la prueba manual y la etapa de potencia están en [docs/info.md](docs/info.md). `info.yaml` define las fuentes, el top y el pinout.

## Pruebas y GDS

- **Actions → test** ejecuta las pruebas de interfaz cocotb y los seis bancos Verilog originales. El artifact `test-results` contiene la traza FST, XML y logs de los bancos.
- **Actions → gds → Run workflow** ejecuta la acción oficial de TTGF26c con `gf180mcuD`. Tras el hardening, se ejecutan el precheck y la simulación a nivel de puertas. `GDS_logs`, `tt_submission`, `gds_render`, `precheck_reports` y `gatelevel_test_results` son los artifacts esperados según qué trabajos terminen. `tt_submission` es el nombre de un artifact local a la corrida de CI.
- Con Icarus, Python y las dependencias de [test/requirements.txt](test/requirements.txt), ejecutar `cd test && bash run_verilog.sh && make && python -m cocotb_tools.check_results results.xml`.

La estructura de configuración, workflows y pruebas a nivel de puertas se adaptó de la plantilla GF180. La única variación de configuración de LibreLane es el período nominal de 1000 ns. El workflow GDS se inicia manualmente y usa el PNG entregado como artifact para visualización. [Plantilla oficial](https://github.com/TinyTapeout/ttgf-verilog-template) · [Configuración original](https://github.com/TinyTapeout/ttgf-verilog-template/blob/main/src/config.json) · [Acción TTGF26c](https://github.com/TinyTapeout/tt-gds-action/tree/ttgf26c)

[Licencia de los componentes adaptados de la plantilla](LICENSE.template).

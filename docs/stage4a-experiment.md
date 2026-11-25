# GF180 C02a · Corrección del verificador

`c02a-verifier-fix` desciende directamente de C02, `78fbad59a2fd8b63d59f5cfc96e26c2dd85de06c`. El netlist físico con alimentación de GF180 declara `I`, `Z`, `VDD`, `VSS`, `VNW` y `VPW` para `eco_buffer_0`. La comparación estricta exige `VDD` y `VNW` en `VPWR`, y `VSS` y `VPW` en `VGND`. La vista lógica exige exactamente `I` y `Z`. El CDL del buffer define los cuerpos PMOS en `VNW` y NMOS en `VPW`. La Liberty utilizada para síntesis declara los mismos terminales y su relación con las dos líneas de alimentación.

La reproducción local usó las mismas evidencias de C02 y reprodujo el único error del verificador original. C02a aceptó esas evidencias y rechazó mutaciones de señal, alimentación, configuración, métricas y RTL. [GDS C02](https://github.com/Leonardo-DeLeon/ttgf-stepper-motor-controller/actions/runs/45581126610/attempts/1) · [Regresión C02](https://github.com/Leonardo-DeLeon/ttgf-stepper-motor-controller/actions/runs/45580828585)

`src/config.json` conserva SHA-256 `6b90fa929d752d3d3b5884037a4884717a617f69d202135690d36305aaf99a9e`. El mismo ECO, periodo de 1000 ns, tile oficial, límites de 3 ns, 0.200 pF y fanout 10, y los nueve corners estrictos siguen vigentes. El [manifiesto](../experiments/stage4a/manifest.json) fija el padre, las 20 entradas verificadas y la procedencia C02.

En `experiment/stage4a-electrical`, esperar la regresión **test** del mismo SHA y luego disparar **gds** manualmente. El cierre requiere verificador, Precheck 12/12 y Gate Level ≥2/2, además de los criterios físicos y eléctricos del manifiesto. En C02, Precheck y Gate Level quedaron omitidos por el fallo del verificador.

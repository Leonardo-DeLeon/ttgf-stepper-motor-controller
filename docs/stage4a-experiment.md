# GF180 C02 · ECO de slew después de Post-GRT

C02 parte de C01 (`440d2e9806ec43f0b3547496598f3d55edb9f5d9`). C01 dejó diez observaciones de slew en cinco pines de una sola red, `_167_`, conducida por `_354_/ZN` (`nor2_1`). El peor valor extraído fue 3.178624 ns en max SS. C01 ocupó 9 961.8176 µm² de área funcional y auxiliar.

El único cambio físico es un buffer no inversor GF180 `buf_1` después de `_354_/ZN`. LibreLane 3.0.14 ejecuta `Odb.InsertECOBuffers` después de Post-GRT y antes del routing detallado. El paso conserva el NOR, crea una red corta hacia la entrada del buffer y conecta la salida del buffer a `_167_` con sus cuatro receptores originales. Legaliza el buffer, actualiza las guías y escribe ODB y DEF. El flujo continúa con routing detallado, extracción y signoff de nueve corners. Los márgenes Post-GRT siguen en 10 %. Permanecen vigentes 3 ns, 200 fF, fanout 10, la raíz CTS `clkbuf_8`, 1 MHz y el tile oficial 1×1.

El verificador exige la firma del NOR y las cuatro cargas, el master y las redes reales del ECO, su cadena de ODB hasta RCX, alimentación en el netlist enrutado, evidencia de GDS y las comprobaciones de signoff. Registra cambios frente a C01 y conserva evidencia de corridas fallidas. Despachar `gds` manualmente en `experiment/stage4a-electrical` después de la regresión `test` del mismo SHA. Se requieren RTL 198/198 y 2/2, Precheck 12/12 y Gate Level ≥2/2. C02 queda pendiente de medición física.

[Inserción registrada en LibreLane 3.0.14](https://github.com/librelane/librelane/blob/3.0.14/librelane/steps/odb.py) · [Implementación ECO](https://github.com/librelane/librelane/blob/3.0.14/librelane/scripts/odbpy/eco_buffer.py)

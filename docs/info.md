# Cómo funciona

El proyecto captura dirección, modo, velocidad y distancia al activar RUN. Genera cuatro fases lógicas, con un intervalo de 10, 8, 6 o 4 ms a 1 MHz. Las fases controlan las entradas de un ULN2003 externo. El ULN2003 conmuta las cuatro bobinas de un 28BYJ-48 de 5 V.

El reset y `ena=0` apagan las fases y limpian BUSY y DONE. Las salidas reservadas valen cero. Los ocho `uio` se dejan como entradas sin uso y sus salidas internas valen cero.

# Cómo probarlo

1. Aplique 1 MHz a clk. Mantenga rst_n bajo durante al menos dos ciclos y RUN bajo antes de volver a habilitar el proyecto. Observe salidas en cero.
2. Libere el reset con ena alto y mantenga RUN bajo cinco flancos. Fije la configuración durante tres períodos antes y tres después de subir RUN.
3. Con DIR=0, MODE=0, SPEED=11 y DISTANCE=001, observe 1100 al subir BUSY. Cada 4 ms observe 0110, 0011, 1001 y 1100, en orden IN1 a IN4. Tras 64 transiciones y un intervalo final, verifique BUSY=0 y DONE=1.
4. Baje RUN. DONE se limpia tras atravesar el sincronizador de RUN. Desactive ena durante un movimiento y verifique salidas en cero y bloqueo hasta observar RUN bajo tras volver a habilitar.

# Hardware externo

ULN2003 y 28BYJ-48 de 5 V. Verifique niveles lógicos, corriente de entrada, tierra común y COM al positivo del motor antes de conectarlos. Mantenga los 5 V del motor en el circuito de potencia.

# TinyTask Orchestrator

Encadená y automatizá tus scripts de TinyTask — para lo que necesites: productividad, testing, tareas repetitivas, gestión de ventanas, o lo que se te ocurra.

TinyTask Orchestrator toma scripts compilados a `.exe` con [TinyTask](https://tinytask.net) y los ejecuta en secuencia con control fino de repeticiones, pausas, y loops globales.

---

## 🚀 Descarga rápida

👉 **[Descargar TinyTaskOrchestrator.exe](https://github.com/roderick850/TinyTaskOrchestrator/releases/latest)** — ~11 MB, portable, no requiere instalación.

> 💡 Si Windows SmartScreen te bloquea, hacé clic en **"Más información"** → **"Ejecutar de todas formas"**. El archivo es seguro, es un falso positivo porque no está firmado digitalmente.

---

## Requisitos

- **Windows** (10/11)
- **Python 3.10+** (solo si ejecutás desde el código fuente)
- [TinyTask](https://tinytask.net) — para grabar y compilar tus macros a `.exe`

### Dependencia Python

| Paquete | Uso |
|---------|-----|
| `keyboard` | Hotkey global para iniciar/detener |

---

## Instalación

### Opción 1: Ejecutable compilado (recomendado)

Descargá `TinyTaskOrchestrator.exe` desde la sección de **[Releases](https://github.com/roderick850/TinyTaskOrchestrator/releases)**. Es portable — no necesita Python ni nada instalado. Solo descargá, ejecutá, y listo.

### Opción 2: Desde el código fuente

```bash
git clone https://github.com/roderick850/TinyTaskOrchestrator.git
cd TinyTaskOrchestrator
pip install -r requirements.txt
python main.py
```

### Opción 3: Compilar tu propio .exe

```bash
pip install pyinstaller
pyinstaller --clean TinyTaskOrchestrator.spec
```

El `.exe` se genera en `dist/TinyTaskOrchestrator.exe`.

---

## Funciones

### 🎯 Encadenamiento de scripts
Agregá múltiples macros de TinyTask y ejecutalas una tras otra en el orden que quieras. Ideal para workflows de varios pasos.

### 🔢 Control fino por script
Cada script de la lista tiene 3 parámetros configurables:

| Parámetro | Descripción |
|-----------|------------|
| **Repeticiones** | Cuántas veces se ejecuta ese script |
| **Duración (s)** | Cuánto tarda en completarse (para calcular tiempos) |
| **Pausa (s)** | Espera entre cada repetición |

### 🔄 Modos de loop global
- **Una vez** — ejecuta toda la lista y termina
- **Fijo** — repite la lista completa N veces
- **Infinito** — repite hasta que lo detengas manualmente

### ✏️ Edición inline rápida
Doble clic en cualquier celda de Repeticiones, Duración o Pausa para editarla directamente sin abrir ventanas emergentes. Enter para guardar, Escape para cancelar.

### ✅ Habilitar / Deshabilitar scripts
Clic en el checkbox ✅/❌ de cada script para activarlo o saltarlo sin borrarlo de la lista.

### ⏱️ Tiempo estimado total
La interfaz calcula y muestra el tiempo estimado de ejecución basado en las duraciones y repeticiones configuradas.

### 📊 Barra de progreso + countdown
Durante la ejecución ves una barra de progreso, porcentaje completado, y un countdown regresivo con el tiempo restante estimado.

### ⌨️ Hotkey global configurable
Configurá una tecla rápida (F5–F12) para iniciar o detener la ejecución sin enfocar la ventana. Funciona aunque el programa esté en segundo plano.

### 🔼🔽 Reordenar scripts
Movelos arriba o abajo con los botones para cambiar el orden de ejecución.

### 💾 Persistencia automática
La lista de scripts, configuraciones y hotkey se guardan automáticamente al cerrar en `playlist.json`.

---

## Cómo usar

1. **Grabá** tus macros en TinyTask y compilalas a `.exe` (botón ⚙️ *Compile*)
2. **Abrí** TinyTask Orchestrator
3. **Agregá** scripts con el botón ➕ y configurá repeticiones, duración y pausa
4. **Ordenalos** con ⬆️⬇️ según el orden en que deben ejecutarse
5. **Configurá** el loop global (una vez, N repeticiones, o infinito)
6. **Ejecutá** con ▶️ *Iniciar todo* o con la hotkey
7. **Detené** en cualquier momento con ⏹️ *Detener* o la hotkey

### Flujo de ejecución

```
Script 1 (×N reps) → Script 2 (×N reps) → ... → [pausa entre loops] → repetir
```

- Cada script se ejecuta la cantidad de veces configurada
- Entre cada script hay un buffer de lanzamiento automático
- Si configuraste *pausa entre loops*, espera ese tiempo antes del siguiente ciclo

---

## Archivos

| Archivo | Descripción |
|---------|------------|
| `main.py` | Punto de entrada |
| `gui.py` | Interfaz gráfica (tkinter) |
| `executor.py` | Motor de ejecución con hilos |
| `config_manager.py` | Carga/guarda `playlist.json` |
| `hotkey.py` | Listener de tecla global |
| `playlist.json` | Configuración persistente |
| `TinyTaskOrchestrator.spec` | Spec de PyInstaller |

---

## Tips

- **Duración estimada:** poné siempre un valor realista (ni muy corto ni muy largo). El orquestador usa este número para calcular tiempos totales y la barra de progreso.
- **Pausa:** útil si el script necesita que la app destino termine de procesar antes de repetir.
- **Loop infinito + pausa entre loops:** ideal para tareas que corren todo el día con descansos programados.
- **Deshabilitar scripts:** usá el checkbox ✅/❌ para probar distintas combinaciones sin borrar ni reconfigurar.

# Voz local de Jarvis (Piper)

Esta carpeta lleva la voz local (sin internet) que Jarvis usa si fallan ElevenLabs y Edge.
Los archivos **no están en el repositorio** (pesan ~63 MB):

- `es_MX-claude-high.onnx`
- `es_MX-claude-high.onnx.json`

Es una voz pública del proyecto Piper. Para bajarla (PowerShell, desde esta carpeta):

```powershell
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/claude/high"
Invoke-WebRequest "$base/es_MX-claude-high.onnx" -OutFile es_MX-claude-high.onnx
Invoke-WebRequest "$base/es_MX-claude-high.onnx.json" -OutFile es_MX-claude-high.onnx.json
```

Si no están, Jarvis funciona igual: usa la voz de Windows como último respaldo (más robótica).

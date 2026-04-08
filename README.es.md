# joshua-agent

**Sprints de software autónomos con gate.**

| Señal | Estado |
| --- | --- |
| Paquete | `0.6.1` |
| Repo | `50 commits` en `main` |
| Tests | `301 tests de pytest`; CI en Python `3.11`, `3.12`, `3.13` |
| Release path | GitHub Actions CI + workflow de publicación a PyPI |

## Demo

https://github.com/jorgevazquez-vagojo/joshua-agent/assets/demo_es.mp4

[🇬🇧 Watch demo in English](assets/demo_en.mp4)

Define tu equipo en YAML. Los agentes trabajan en ciclos — dev, bug-hunter, QA. El gate decide:  despliega.  revierte. Vuelves y tienes un log.

*Algún día, los equipos dejarán de hacer de niñera de la IA. En vez de ir prompt a prompt — copiar, pegar, revisar, repetir — definirán un equipo en un archivo YAML y se irán a dormir. Los agentes trabajan en ciclos: ejecutan tareas, se revisan entre sí, despliegan o revierten, extraen lecciones, duermen, repiten. Vuelves y tienes un log de lo que pasó y (con suerte) un resultado mejor que ayer. — @jorgevazquez, abril 2026*

La idea: define un equipo de agentes IA como **skills** en YAML, apúntalos a una carpeta (código, documentos, informes — lo que sea), y déjalos correr de forma autónoma en ciclos. En cada ciclo, los agentes de trabajo ejecutan sus tareas. Los agentes de puerta revisan el resultado y emiten un veredicto: `GO`, `CAUTION` o `REVERT`. El trabajo malo se revierte automáticamente. El bueno se despliega. Los agentes aprenden en cada ciclo — las lecciones se acumulan, una wiki se construye sola, y los prompts futuros mejoran automáticamente. Tú duermes. Ellos trabajan.

Nombrado como la IA de WarGames que aprendió que la única jugada ganadora es seguir jugando.

```

## Estado actual

**Estable**

- Sprints multiagente definidos en YAML con fases `work` y `gate`
- Bucle de veredictos `GO` / `CAUTION` / `REVERT` con estrategias git `snapshot` o `hillclimb`
- Flujo CLI: `joshua run`, `joshua status`, `joshua evolve`, `joshua serve`
- Plano de control HTTP, runtime basado en procesos, persistencia, notificaciones y recuperación tras reinicio
- Configuración de seguridad con allowlists de comandos/rutas, archivos protegidos, métricas objetivas y políticas explícitas por veredicto

**Experimental**

- Despliegues desatendidos sobre infraestructura de producción real
- Calidad de la wiki autoaprendida y evolución de prompts en sprints largos
- Modos `event` y `on_demand` cuando dependen de task sources y hooks propios
- Runners custom y cadenas de hooks que ejecutan shell arbitrario
 Skills de trabajo          Skills de puerta
+--------------+          +----------+
| Dev          |          |          |
| Bug Hunter   |--------->|   QA     |--> Deploy (o Revert)
| CFO          |          | Review   |
| Cualquiera...|          +----------+
+--------------+               |
       ^                       |
       +---- siguiente ciclo --+
```

## Cómo funciona

joshua-agent tiene tres conceptos fundamentales:

- **Skills** — un skill es cualquier rol profesional que puedas describir en un prompt. `dev`, `qa`, `bug-hunter`, `security`, `cfo`, `legal-analyst`, `compliance`, `pm`, `tech-writer`, o literalmente cualquier otra cosa. Los skills integrados son solo plantillas de prompt. Puedes definir los tuyos con `system_prompt:` en YAML — si puedes briefear a una persona, puedes briefear a un agente.
- **Fases** — los agentes son `work` (ejecutan tareas) o `gate` (revisan y juzgan). Los agentes de trabajo producen output. Los agentes de puerta leen ese output y devuelven un veredicto: `GO` (adelante), `CAUTION` (adelante pero con flag), o `REVERT` (revertir). Esta separación existe porque el output de IA sin supervisión es peligroso. La puerta es un interruptor de seguridad.
- **Ciclos** — los agentes no corren una vez. Ciclan. Cada ciclo toma la siguiente tarea (round-robin), ejecuta todos los agentes de trabajo, alimenta el output a los agentes de puerta, actúa según el veredicto, extrae lecciones, y duerme. Luego lo repite. Así funcionan los equipos reales — mejora continua, no esfuerzos heroicos puntuales.

La abstracción del runner significa que a joshua-agent le da igual qué LLM uses. Claude Code, OpenAI Codex, Aider, o cualquier herramienta CLI. Cámbialo en el YAML y todo lo demás sigue igual.


## Métricas & Evaluación

En cada ciclo de sprint, joshua registra:

- **Número de ciclo** — contador secuencial desde que arrancó el sprint
- **Duración de agentes** — segundos reales que tardó cada agente en ejecutarse
- **Veredicto de puerta** — `GO`, `CAUTION` o `REVERT` para el ciclo
- **Errores consecutivos** — cuántos ciclos seguidos han acabado en fallo o error
- **Hallazgos de puerta** — el texto que devolvió el agente de puerta, inyectado en el siguiente ciclo

Los resultados se almacenan en el directorio `.joshua/` junto a tu proyecto:

```
.joshua/
├── checkpoint.json     Ciclo actual, último veredicto, contadores de error
├── lessons/            Un archivo por ciclo — lecciones extraídas del output del agente
└── wiki/               Entradas de conocimiento curadas a partir de las lecciones acumuladas
```

Para medir el progreso a lo largo de ciclos, usa el comando de estado:

```bash
joshua status .joshua
```

Muestra el historial de ciclos, distribución de veredictos y tiempos por agente. Compara ciclo 1 vs ciclo N para ver si la puerta emite menos REVERTs y los agentes completan tareas más rápido.

Para evolucionar los prompts de los agentes usando las lecciones acumuladas:

```bash
joshua evolve config.yaml
```

`joshua evolve` cura las lecciones en entradas de wiki y puede reescribir los prompts de los agentes para incorporar lo aprendido.

**Nota honesta:** No existe un dataset de benchmark público para joshua-agent. Lo que sí puedes medir en tu propio proyecto: ratio GO/REVERT a lo largo del tiempo, duración de agentes ciclo a ciclo, y patrones en los hallazgos de la puerta. Usa `joshua status` para construir tu propia línea base.

## Inicio rápido

```bash
pip install joshua-agent
```

**Ejemplo 1 — Sprint de desarrollo safe by default.** Empieza con un script wrapper controlado por ti. Mete build, tests, migraciones y health checks ahí, en vez de enchufar shell ad hoc en la primera demo.

```yaml
# dev-sprint.yaml
project:
  name: my-app
  path: ~/my-app
  deploy: "./deploy.sh"   # Empieza aquí: encapsula el despliegue en un único script

agents:
  dev:
    skill: dev
    tasks:
      - "Review code quality and suggest improvements"
      - "Refactor for maintainability"
  bug-hunter:
    skill: bug-hunter
    tasks:
      - "Scan for uncaught exceptions and error handling gaps"
  qa:
    skill: qa

sprint:
  cycle_sleep: 600
```

Usa un wrapper para los despliegues. Hace el primer sprint más reproducible y te deja añadir tests, health checks, migraciones o rollback hooks sin reescribir el YAML.

**Ejemplo 2 — Sprint ejecutivo.** Sin código. Sin comando de deploy. Los agentes analizan documentos, auditan costes y verifican cumplimiento normativo. Mismo framework, distintos skills.

```yaml
# executive.yaml
project:
  name: acme-corp
  path: ~/acme-corp-docs

agents:
  cfo:
    skill: cfo
    system_prompt: |
      You are {agent_name}, CFO for {project_name}.
      Analyze financial documents in {project_dir}.
    tasks:
      - "Audit vendor contracts expiring within 90 days"
      - "Analyze monthly burn rate from financial reports"
  compliance:
    skill: compliance
    phase: gate
    verdict_format: true
    system_prompt: |
      You are {agent_name}, Compliance Director.
      Review all analysis for regulatory compliance.

sprint:
  cycle_sleep: 600
  gate_blocking: true
```

```bash
joshua run dev-sprint.yaml    # Sprint de software
joshua run executive.yaml     # Sprint de análisis de negocio
```

Los agentes trabajan, la puerta revisa, se actúa según el veredicto. Repite. Cualquier dominio, cualquier rol.

### Cómo se ve

```
============================================================
CYCLE 1 — 2026-04-05T03:14:00
============================================================
[cfo] (cfo) Task: Audit vendor contracts expiring within 90 days
[cfo] OK (189.3s, 3841 chars)
[compliance] (compliance) Reviewing cycle 1...
[compliance] OK (94.2s, 1102 chars)
VERDICT: GO
CYCLE 1 COMPLETE — verdict=GO
Sleeping 600s before next cycle...
```

## Decisiones de diseño

**Skills, no roles.** Cada agente es un skill definido en YAML. Los skills integrados (`dev`, `qa`, `bug-hunter`, `security`, `perf`, `pm`, `tech-writer`) son puntos de partida — plantillas de prompt con defaults razonables. Pero el poder real está en los skills personalizados: un CFO que audita costes, un analista legal que revisa contratos, un director de cumplimiento que verifica gobernanza, un COO que mapea cuellos de botella operativos. Sin comando de deploy. Sin código. joshua-agent no es una herramienta de código que también soporta otras cosas. Es un framework para trabajo profesional autónomo que además es bueno programando.

**Dos fases: trabajo y puerta.** Los agentes de trabajo hacen el trabajo. Los agentes de puerta lo juzgan. Esta es la decisión de diseño más importante del framework. Sin puerta, simplemente estás ejecutando IA sin supervisión y esperando lo mejor. La puerta es un interruptor de seguridad — `REVERT` significa que nada se despliega. En producción, hemos visto cómo los agentes de puerta detectan problemas que habrían roto despliegues, señalado análisis no conformes, y prevenido errores en cascada. El modelo de dos fases también permite escalar agentes de trabajo independientemente de la capacidad de revisión.

**Ciclos continuos, no one-shot.** La mayoría de frameworks de agentes ejecutan una vez y paran. joshua-agent cicla. Cada ciclo toma la siguiente tarea de una cola round-robin, así que un agente dev con 10 tareas las trabajará todas a lo largo de 10 ciclos. Después de cada ciclo, los agentes extraen lecciones de su output. Qué funcionó, qué falló, qué patrones seguir o evitar. Estas lecciones se acumulan y se inyectan en los prompts futuros. Los agentes literalmente mejoran con el tiempo. Hemos observado mejora medible en la calidad del output entre el ciclo 1 y el ciclo 10 en el mismo proyecto.

**Auto-aprendizaje vía wiki (patrón Karpa).** El output crudo de cada ciclo se guarda. Periódicamente, el LLM cura ese output crudo en entradas de conocimiento estructuradas — una wiki que se construye sola. Las entradas se deduplicean, se verifican por contradicciones, y se retroalimentan a los agentes como contexto. Tú nunca escribes la wiki. El LLM lo escribe todo. Tú solo diriges — cada respuesta se compone en conocimiento institucional.

**Agnóstico de LLM.** joshua-agent habla con herramientas CLI, no con APIs. Claude Code, OpenAI Codex, Aider, o cualquier comando personalizado que acepte un prompt y devuelva texto. El runner es una interfaz de un solo método: `run(prompt, cwd, system_prompt, timeout) -> RunResult`. Cámbialo en YAML, todo lo demás sigue igual. Esto significa que puedes usar diferentes modelos para diferentes agentes — Opus para la puerta, Sonnet para los agentes de trabajo, un modelo local para experimentos.

**Bloqueo de puerta.** Cuando una puerta dice `REVERT`, probablemente no quieres que los agentes de trabajo apilen más cambios encima. `gate_blocking: true` congela los agentes de trabajo en el siguiente ciclo. Solo los agentes marcados con `run_when_blocked: true` (como los cazadores de bugs y escáneres de seguridad) correrán. Esto previene fallos en cascada — el cazador de bugs arregla lo que la puerta señaló, la puerta revisa el fix, y solo entonces se reanuda el trabajo normal.

**Contexto entre agentes.** Los hallazgos de la puerta del ciclo anterior se inyectan en los prompts de los agentes de trabajo vía `{gate_findings}`. El agente QA le dice al agente dev qué está mal. El agente dev lo arregla en el siguiente ciclo. Se comunican a través del framework — sin copiar y pegar manual, sin pérdida de contexto entre ejecuciones.

**Planificación consciente de recursos.** Cada agente LLM consume memoria significativa. Ejecutar múltiples sprints en la misma máquina puede provocar OOM kills (lo aprendimos por las malas). `min_memory_gb` comprueba la RAM disponible antes de cada ejecución de agente — si la memoria es baja, joshua-agent espera en vez de crashear. `agent_stagger` añade un retardo fijo entre ejecuciones de agentes para dejar que el sistema respire. Juntos, permiten ejecutar múltiples sprints de forma segura en un solo servidor.

**Métricas objetivas.** Los agentes de puerta son buenos en revisión cualitativa, pero no reemplazan a un test suite. `project.objective_metric` define un comando shell que devuelve un número (menor = mejor). joshua-agent lo ejecuta antes y después de los agentes de trabajo, inyecta el delta en el prompt de la puerta, y registra ambos valores en `results.tsv`. La puerta ahora tiene datos duros junto a su juicio cualitativo. Piensa en `pytest --tb=no -q`, un script de benchmark, o cualquier comando que imprima un número.

**Archivos protegidos.** `project.protected_files` lista globs que los agentes de trabajo no deben modificar. La instrucción se inyecta directamente en el prompt de tarea: "DO NOT modify: tests/\*\*, eval.py". Esto evita que los agentes "engañen" a la métrica editando la evaluación o el harness de tests — el mismo patrón que Karpathy usa en autoresearch donde `prepare.py` es read-only.

**Estrategia git hillclimb.** `git_strategy: hillclimb` convierte git en un checkpoint de hill-climbing. Antes de cada ciclo, joshua-agent commitea el estado actual. Después de que los agentes trabajen y la puerta revise, un veredicto `REVERT` dispara `git reset --hard` al checkpoint. Un veredicto `GO` conserva el commit. El resultado: cada commit superviviente en el historial git es una mejora verificada. Comparado con `snapshot`, que crea ramas por ciclo, hillclimb es más simple y produce un historial lineal.

**Tres modos de trigger.** `sprint.trigger` controla cuándo se ejecutan los ciclos. `continuous` (por defecto) ejecuta ciclos continuamente con `cycle_sleep` entre ellos — ideal para mejora proactiva. `event` consulta los task sources (Jira, GitHub) cada `poll_interval` segundos y solo ejecuta un ciclo cuando hay trabajo real — sin tareas, sin llamadas a Claude, sin tokens quemados. `on_demand` espera un trigger externo vía API (`trigger_cycle()`) — útil para integración CI/CD donde un deploy o PR dispara una revisión.

## Runners soportados

| Runner | Comando | Instalación |
|--------|---------|-------------|
| **Claude Code** | `claude` | `npm i -g @anthropic-ai/claude-code` |
| **OpenAI Codex** | `codex` | `npm i -g @openai/codex` |
| **Aider** | `aider` | `pip install aider-chat` |
| **Custom** | cualquier CLI | `command: "my-tool --input {prompt_file} --dir {cwd}"` |

## Referencia completa de configuración

```yaml
project:
  name: mi-proyecto
  path: ~/mi-proyecto              # Cualquier carpeta — código, docs, informes, datos
  deploy: "bash deploy.sh"         # Opcional — omitir para sprints sin código
  health_url: http://localhost:3000/health  # Opcional
  objective_metric: "pytest --tb=no -q | tail -1"  # Comando que imprime un número (menor = mejor)
  protected_files:                  # Globs que los agentes NO deben modificar
    - "tests/**"
    - "eval.py"

program: |                          # Opcional — contexto compartido para TODOS los agentes
  ## Objetivo
  Reducir latencia p95 por debajo de 200ms.
  ## Restricciones
  - NO modificar el esquema de base de datos
  - Solo editar archivos en src/api/

runner:
  type: claude                  # claude | codex | aider | custom
  timeout: 1800                 # Segundos máximos por ejecución de agente
  model: sonnet                 # Override de modelo (opcional)

agents:
  dev:
    name: falken                # Nombre personalizado (opcional)
    skill: dev                  # Skill integrado o personalizado
    max_changes: 5              # Máx cambios por ciclo
    run_when_blocked: false     # Ejecutar incluso cuando la puerta está bloqueada
    tasks:
      - "Tarea 1"
      - "Tarea 2"              # Round-robin a través de la lista

  qa:
    skill: qa                   # Los skills de puerta auto-detectan formato de veredicto

  cfo:
    skill: cfo
    system_prompt: |            # Cualquier prompt que quieras
      You are {agent_name}, a CFO reviewing {project_name}.
      Analyze costs, licensing, and resource usage.
    tasks:
      - "Audit third-party dependency costs"

sprint:
  trigger: continuous           # continuous | event | on_demand
  poll_interval: 300            # Segundos entre polls (modos event/on_demand)
  cycle_sleep: 300              # Segundos entre ciclos
  max_cycles: 0                 # 0 = infinito
  max_hours: 96                 # 0 = infinito
  digest_every: 12              # Informe resumen cada N ciclos
  retries: 2                    # Reintentos de ejecuciones fallidas
  revert_sleep: 600             # Pausa más larga después de REVERT
  max_consecutive_errors: 5     # Parar después de N errores consecutivos
  gate_blocking: true           # REVERT bloquea agentes de trabajo
  cross_agent_context: true     # Hallazgos de puerta -> agentes de trabajo
  health_check: true            # Verificar health_url cada ciclo
  recovery_deploy: "bash rollback.sh"
  git_strategy: hillclimb       # none | snapshot | hillclimb
  agent_stagger: 30             # Segundos de espera entre ejecuciones de agentes
  min_memory_gb: 4              # Esperar por RAM libre antes de cada agente

preflight:
  min_disk_gb: 5                # Verificar disco antes de cada ciclo
  min_memory_gb: 4              # Verificar RAM antes de cada ciclo
  memory_wait_timeout: 120      # Segundos de espera si la memoria es baja
  docker_cleanup: true          # Auto-limpiar Docker con disco bajo

notifications:
  type: telegram                # telegram | slack | webhook | none
  token: ${TELEGRAM_TOKEN}
  chat_id: ${TELEGRAM_CHAT_ID}

tracker:
  type: jira                    # jira | github | filesystem | none
  base_url: https://x.atlassian.net
  project_key: PROJ

memory:
  enabled: true
  state_dir: .joshua
```

Variables disponibles en prompts de agentes: `{agent_name}`, `{skill}`, `{project_name}`, `{project_dir}`, `{deploy_command}` (de `project.deploy`), `{program}` (de `program:` top-level), `{memory}`, `{wiki}`, `{gate_findings}`, `{max_changes}`.

Cada ciclo añade una fila a `.joshua/results.tsv` — un log plano, greppable, que no necesita el CLI:

```
cycle  verdict  duration_s  agents          confidence  metric_before  metric_after  description
1      GO       284.1       dev,bug-hunter  0.94        12.3           8.1           Fixed SQL injection...
2      REVERT   312.0       dev,qa          0.97        8.1            15.2          Auth middleware broke...
```

## CLI

```bash
joshua run config.yaml              # Ejecutar un sprint
joshua run config.yaml -n 10        # Máximo 10 ciclos
joshua run config.yaml -H 96        # Máximo 96 horas
joshua run config.yaml --dry-run    # Validar config sin ejecutar
joshua status .joshua               # Panel de estado
joshua evolve config.yaml           # Ejecutar evolución + mantenimiento de wiki
```


> **Seguridad en deploy**: `project.deploy` se ejecuta como un comando de shell con los permisos de tu usuario. Los metacaracteres de shell (`;`, `|`, `` ` ``, `$(`) son rechazados por la validación de config. Usa el modo dry-run (`joshua run config.yaml --dry-run`) para validar antes de ejecutar. Nunca uses configs YAML de fuentes no confiables.

## Ejemplos

Ver [`examples/`](examples/) para configs listas para usar:

**Negocio y gobernanza:**
- [`executive-team.yaml`](examples/executive-team.yaml) — CFO + COO + Director de Cumplimiento
- [`legal-review.yaml`](examples/legal-review.yaml) — Analista Legal + Evaluador de Riesgos + Abogado General

**Desarrollo de software:**
- [`minimal.yaml`](examples/minimal.yaml) — 3 agentes, cero config
- [`full-team.yaml`](examples/full-team.yaml) — Dev, Bug Hunter, Security, Perf, PM, QA
- [`wordpress.yaml`](examples/wordpress.yaml) — WordPress: WCAG, SEO, auditorías PHP
- [`nextjs.yaml`](examples/nextjs.yaml) — Next.js: TypeScript, React, auditorías API
- [`python-api.yaml`](examples/python-api.yaml) — FastAPI/Django: testing, seguridad, auditorías DB

## Casos de uso

Tres packs listos para ejecutar en escenarios habituales:

### Pack 1: Modernización de código legacy

Agentes: `dev` (modernizar código), `bug-hunter` (detectar regresiones), `qa` (revisión de puerta).
Cada ciclo mejora un área del código legacy. La puerta bloquea el siguiente ciclo si los tests fallan o aparecen regresiones, de modo que los cambios se acumulan de forma segura. Ejemplo: [`examples/python-api.yaml`](examples/python-api.yaml).

### Pack 2: Puerta de release continua

Agentes: `dev` (implementar feature o fix), `qa` (puerta de calidad con GO/CAUTION/REVERT).
Ejecuta el equivalente a tu CI de forma autónoma — despliega automáticamente en GO, revierte en REVERT, duerme y repite. Sustituto directo de un revisor humano en ramas de bajo riesgo. Ejemplo: [`examples/minimal.yaml`](examples/minimal.yaml).

### Pack 3: Revisión documental y de cumplimiento

Agentes: `analyst` (revisar documentos), `legal` (verificación de cumplimiento), `executive` (resumen + puerta).
Ciclo de revisión multi-agente para contratos, políticas o especificaciones técnicas. Sin comando de deploy — el veredicto de la puerta determina si el documento pasa o requiere revisión. Ejemplo: [`examples/legal-review.yaml`](examples/legal-review.yaml).

## Arquitectura

```
joshua/
├── cli.py              Punto de entrada CLI
├── config.py           Cargador YAML + interpolación ${ENV}
├── sprint.py           El bucle (trabajo → puerta → deploy/revert → aprender → dormir → repetir)
├── agents.py           Definiciones de skills + plantillas de prompt
├── runners/
│   ├── base.py         Interfaz LLMRunner
│   ├── claude.py       Claude Code
│   ├── codex.py        OpenAI Codex
│   ├── aider.py        Aider
│   └── custom.py       Cualquier herramienta CLI
├── memory/
│   ├── lessons.py      Extraer lecciones de cada ciclo
│   ├── wiki.py         Base de conocimiento patrón Karpa
│   └── evolve.py       Evolución diaria + lint
├── integrations/
│   ├── git.py          Snapshot, merge, revert
│   ├── notifications.py Telegram, Slack, webhook
│   └── trackers.py     Jira, GitHub Issues, filesystem
└── utils/
    ├── health.py       Checks de salud HTTP
    ├── preflight.py    Disco, memoria, limpieza Docker
    └── status.py       Panel de estado
```

## Contribuir

Áreas donde se necesita ayuda:

- **Runners**: Cursor, Windsurf, VS Code Copilot
- **Trackers**: Linear, Notion, Trello
- **Notificadores**: Discord, email, PagerDuty
- **Skills**: comparte tus plantillas de skills personalizados

## Licencia

MIT. Ver [LICENSE](LICENSE).

---

Hecho por [Jorge Vazquez](https://github.com/jorgevazquez). La única jugada ganadora es seguir jugando.

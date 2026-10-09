"""Memory-driven dodge timing for Swords & Souls Neverseen agility training."""

from __future__ import annotations

from collections.abc import Callable
import threading

import frida

from wine_frida import AgentErrors, agent_source


_AGENT_SOURCE = r"""
const assemblyForeach = native('mono_assembly_foreach', 'void', ['pointer', 'pointer']);
const assemblyGetImage = native('mono_assembly_get_image', 'pointer', ['pointer']);
const imageGetName = native('mono_image_get_name', 'pointer', ['pointer']);
const classFromName = native('mono_class_from_name', 'pointer',
    ['pointer', 'pointer', 'pointer']);
const classGetField = native('mono_class_get_field_from_name', 'pointer',
    ['pointer', 'pointer']);
const fieldGetOffset = native('mono_field_get_offset', 'int', ['pointer']);
const getMethod = native('mono_get_method', 'pointer',
    ['pointer', 'uint', 'pointer']);
const compileMethod = native('mono_compile_method', 'pointer', ['pointer']);
const liveBegin = native('mono_unity_liveness_calculation_begin', 'pointer',
    ['pointer', 'uint', 'pointer', 'pointer', 'pointer', 'pointer']);
const liveStatics = native('mono_unity_liveness_calculation_from_statics',
    'void', ['pointer']);
const liveEnd = native('mono_unity_liveness_calculation_end', 'void', ['pointer']);
const handleNew = native('mono_gchandle_new_weakref', 'uint', ['pointer', 'bool']);
const handleTarget = native('mono_gchandle_get_target', 'pointer', ['uint']);
const handleFree = native('mono_gchandle_free', 'void', ['uint']);

let active = false;
let leadMs = 32.0;
let fired = false;

function start() {
    let image = ptr(0);
    const assemblyCallback = new NativeCallback(function (assembly, _userData) {
        const candidate = assemblyGetImage(assembly);
        const name = imageGetName(candidate);
        if (!name.isNull() && name.readUtf8String() === 'Assembly-CSharp') {
            image = candidate;
        }
    }, 'void', ['pointer', 'pointer'], abi);
    assemblyForeach(assemblyCallback, ptr(0));
    if (image.isNull()) throw new Error('Assembly-CSharp is not loaded');

    function klass(namespaceName, className) {
        const result = classFromName(
            image,
            Memory.allocUtf8String(namespaceName),
            Memory.allocUtf8String(className));
        if (result.isNull()) throw new Error(namespaceName + '.' + className + ' not found');
        return result;
    }

    function method(row, result, args) {
        const metadata = getMethod(image, 0x06000000 + row, ptr(0));
        if (metadata.isNull()) throw new Error('Method row not found: ' + row);
        return guarded(
            new NativeFunction(compileMethod(metadata), result, args, abi),
            result);
    }

    const trainerClass = klass('Training', 'TrainerAgility');
    const heroClass = klass('Hero', 'HeroAgility');
    const trainingClass = klass('Training', 'TrainingAgility');
    const promptClass = klass('SGlibrary', 'Prompt');
    const noopWorld = new NativeCallback(function () {}, 'void', [], abi);

    function fieldOffset(targetClass, name) {
        const field = classGetField(targetClass, Memory.allocUtf8String(name));
        if (field.isNull()) throw new Error('Field not found: ' + name);
        return fieldGetOffset(field);
    }

    function findOne(targetClass) {
        const found = [];
        const callback = new NativeCallback(function (array, count, _userData) {
            for (let index = 0; index < count; ++index) {
                found.push(array.add(index * Process.pointerSize).readPointer());
            }
        }, 'void', ['pointer', 'int', 'pointer'], abi);
        const state = liveBegin(
            targetClass, 10000, callback, ptr(0), noopWorld, noopWorld);
        liveStatics(state);
        liveEnd(state);
        for (const candidate of found) {
            if (unityAlive(candidate)) return candidate;
        }
        return ptr(0);
    }

    // Exact methods from the shipped Assembly-CSharp build.
    const getAttackDirection = method(3061, 'pointer', ['pointer']);
    const getSwitched = method(3064, 'bool', ['pointer']);
    const getTrainerAnim = method(3139, 'pointer', ['pointer']);
    const getCurrentAnim = method(4268, 'pointer', ['pointer']);
    const getProgress = method(4335, 'float', ['pointer']);
    const promptStartAddress = compileMethod(
        getMethod(image, 0x06000000 + 4783, ptr(0)));
    const trainingPromptOffset = fieldOffset(trainingClass, 'prompt');
    const promptInputOffset = fieldOffset(promptClass, 'input');

    let trainerHandle = 0;
    let heroHandle = 0;
    let promptHandle = 0;
    let lastSignature = '';
    let lastProgress = -1.0;
    let lastSampleTime = 0.0;
    let progressRate = 0.0;
    let lastFireTime = -10000.0;
    let lastPromptTime = -10000.0;

    function text(managedString) {
        if (managedString.isNull()) return '';
        const length = managedString.add(16).readS32();
        if (length <= 0 || length > 256) return '';
        return managedString.add(20).readUtf16String(length);
    }

    function releaseHandles() {
        if (trainerHandle !== 0) handleFree(trainerHandle);
        if (heroHandle !== 0) handleFree(heroHandle);
        if (promptHandle !== 0) handleFree(promptHandle);
        trainerHandle = 0;
        heroHandle = 0;
        promptHandle = 0;
    }

    function discover() {
        releaseHandles();
        const trainer = findOne(trainerClass);
        const hero = findOne(heroClass);
        const training = findOne(trainingClass);
        if (!trainer.isNull()) trainerHandle = handleNew(trainer, 0);
        if (!hero.isNull()) heroHandle = handleNew(hero, 0);
        if (!training.isNull()) {
            const prompt = training.add(trainingPromptOffset).readPointer();
            if (!prompt.isNull()) promptHandle = handleNew(prompt, 0);
        }
        fired = false;
        lastSignature = '';
        lastProgress = -1.0;
        lastSampleTime = 0.0;
        progressRate = 0.0;
        return trainerHandle !== 0 && heroHandle !== 0 && promptHandle !== 0;
    }

    function keyFor(direction, switched) {
        if (direction === 'Down') return 'W';
        if (direction === 'Up') return 'S';
        if (direction === 'Front') return switched ? 'D' : 'A';
        return null;
    }

    // Searching the heap is expensive and, under Wine, runs on the game's own
    // thread, so it is retried only twice per second.
    let lastDiscovery = Date.now();
    function rediscover() {
        if (Date.now() - lastDiscovery < 500) return;
        lastDiscovery = Date.now();
        discover();
    }

    discover();

    // The mannequin counterattack is driven by Prompt.Start(duration, input, ...).
    // Waiting briefly after this call guarantees that Prompt.Update is ready to
    // consume the A/D key.  Filtering by TrainingAgility.prompt prevents unrelated
    // game prompts from producing input.
    Interceptor.attach(promptStartAddress, {
        onEnter(args) {
            if (promptHandle === 0) return;
            const expectedPrompt = handleTarget(promptHandle);
            const promptObject = hookArgument(this, args, 0);
            if (expectedPrompt.isNull() || !promptObject.equals(expectedPrompt)) return;

            let requested = '';
            try {
                requested = text(hookArgument(this, args, 2));
            } catch (_error) {
                requested = '';
            }
            setTimeout(function () {
                if (!active) return;
                let direction = requested;
                if (direction !== 'Left' && direction !== 'Right') {
                    try {
                        direction = text(promptObject.add(promptInputOffset).readPointer());
                    } catch (_error) {
                        return;
                    }
                }
                const key = direction === 'Left' ? 'A'
                    : (direction === 'Right' ? 'D' : null);
                if (key === null) return;
                const now = Date.now();
                if (now - lastPromptTime < 150.0) return;
                lastPromptTime = now;
                send({
                    kind: 'attack',
                    key: key,
                    direction: direction,
                    delayMs: 14
                });
            }, 14);
        }
    });

    send({
        kind: 'ready',
        training: trainerHandle !== 0 && heroHandle !== 0 && promptHandle !== 0,
        leadMs: leadMs
    });

    function scan() {
        if (trainerHandle === 0 || heroHandle === 0) {
            rediscover();
            return;
        }
        const trainer = handleTarget(trainerHandle);
        const hero = handleTarget(heroHandle);
        if (!unityAlive(trainer) || !unityAlive(hero)) {
            rediscover();
            return;
        }

        const animation = getTrainerAnim(trainer);
        if (!unityAlive(animation)) return;
        const animationId = text(getCurrentAnim(animation));
        const direction = text(getAttackDirection(trainer));
        const switched = Boolean(getSwitched(trainer));
        const progress = getProgress(animation);
        const now = Date.now();
        const isAttack = animationId.indexOf('attackAgility') === 0;

        if (!isAttack || !Number.isFinite(progress)) {
            fired = false;
            lastSignature = '';
            lastProgress = -1.0;
            lastSampleTime = now;
            progressRate = 0.0;
            return;
        }

        const signature = direction + '|' + switched + '|' + animationId;
        const reset = signature !== lastSignature ||
            (lastProgress >= 0.0 && progress < lastProgress - 0.35);
        if (reset) {
            fired = false;
            progressRate = 0.0;
            lastProgress = progress;
            lastSampleTime = now;
            lastSignature = signature;
        } else if (progress > lastProgress + 0.00001) {
            const elapsed = now - lastSampleTime;
            if (elapsed > 0.0 && elapsed < 100.0) {
                const measured = (progress - lastProgress) / elapsed;
                progressRate = progressRate > 0.0
                    ? progressRate * 0.65 + measured * 0.35
                    : measured;
            }
            lastProgress = progress;
            lastSampleTime = now;
        }

        if (!active || fired || now - lastFireTime < 90.0) return;
        const key = keyFor(direction, switched);
        if (key === null) return;

        // TrainingAgility.onTrainerHit consistently runs near progress 0.572.
        // Predicting one to two frames forward keeps the dodge both safe and
        // inside the game's <= 0.3 animation-progress perfect-star window.
        const decisionProgress = 0.572;
        const predicted = progress + Math.max(progressRate, 0.0) * leadMs;
        if (progress >= 0.08 &&
                (predicted >= decisionProgress || progress >= 0.565)) {
            fired = true;
            lastFireTime = now;
            send({
                kind: 'dodge',
                key: key,
                direction: direction,
                switched: switched,
                progress: progress,
                predicted: predicted,
                rate: progressRate,
                leadMs: leadMs
            });
        }
    }
    return scan;
}

runOnMonoThread(start, 1);

rpc.exports = {
    setActive(value) {
        active = Boolean(value);
        if (!active) fired = false;
        return active;
    },
    setLeadMs(value) {
        const parsed = Number(value);
        if (Number.isFinite(parsed)) leadMs = Math.max(10.0, Math.min(80.0, parsed));
        return leadMs;
    }
};
"""


class AgilityMemoryReader:
    """Emit a single W/A/S/D event just before each agility hit check."""

    def __init__(
        self,
        pid: int,
        on_dodge: Callable[[dict], None],
        on_status: Callable[[str], None] = print,
    ) -> None:
        self._on_dodge = on_dodge
        self._on_status = on_status
        self._errors = AgentErrors(on_status)
        self._lock = threading.Lock()
        self._closed = False
        # Under Wine this also makes the process safe for Frida to attach.
        source = agent_source(pid, _AGENT_SOURCE)
        self._session = frida.attach(pid)
        self._script = self._session.create_script(source, name="agility")
        self._script.on("message", self._handle_message)
        self._script.load()

    def _handle_message(self, message: dict, _data: bytes | None) -> None:
        if message.get("type") == "error":
            self._errors.report(message)
            return
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return
        if payload.get("kind") == "ready":
            state = "yes" if payload.get("training") else "no"
            self._on_status(f"Agility memory ready: training={state}.")
        elif payload.get("kind") in {"dodge", "attack"}:
            self._on_dodge(payload)
        elif payload.get("kind") == "runtimeError":
            self._on_status(f"Memory runtime error: {payload.get('message')}")

    def set_active(self, active: bool) -> None:
        with self._lock:
            if not self._closed:
                self._script.exports_sync.set_active(bool(active))

    def set_lead_ms(self, lead_ms: float) -> float:
        with self._lock:
            if self._closed:
                return float(lead_ms)
            return float(self._script.exports_sync.set_lead_ms(float(lead_ms)))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._script.unload()
            finally:
                self._session.detach()

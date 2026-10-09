"""Read-only Mono state reader for melee-training threats.

The helper attaches to the running Unity/Mono process, finds the four live
Training.TrainerMelee objects and the live projectile pool.  It reports the
game's own mannequin parry edge and the first ideal-score frame for each
projectile.  It does not write any value in the game process.
"""

from __future__ import annotations

from collections.abc import Callable
import threading

import frida

from wine_frida import agent_source


_AGENT_SOURCE = r"""
const monoAssemblyForeach = native(
    'mono_assembly_foreach', 'void', ['pointer', 'pointer']);
const monoAssemblyGetImage = native(
    'mono_assembly_get_image', 'pointer', ['pointer']);
const monoImageGetName = native(
    'mono_image_get_name', 'pointer', ['pointer']);
const monoClassFromName = native(
    'mono_class_from_name', 'pointer',
    ['pointer', 'pointer', 'pointer']);
const monoClassGetMethodFromName = native(
    'mono_class_get_method_from_name', 'pointer',
    ['pointer', 'pointer', 'int']);
const monoCompileMethod = native(
    'mono_compile_method', 'pointer', ['pointer']);
const monoClassGetParent = native(
    'mono_class_get_parent', 'pointer', ['pointer']);
const monoClassGetFieldFromName = native(
    'mono_class_get_field_from_name', 'pointer',
    ['pointer', 'pointer']);
const monoFieldGetOffset = native(
    'mono_field_get_offset', 'int', ['pointer']);
const monoObjectGetClass = native(
    'mono_object_get_class', 'pointer', ['pointer']);
const monoLivenessBegin = native(
    'mono_unity_liveness_calculation_begin', 'pointer',
    ['pointer', 'uint', 'pointer', 'pointer', 'pointer', 'pointer']);
const monoLivenessFromStatics = native(
    'mono_unity_liveness_calculation_from_statics', 'void',
    ['pointer']);
const monoLivenessEnd = native(
    'mono_unity_liveness_calculation_end', 'void', ['pointer']);
const monoGcHandleNewWeak = native(
    'mono_gchandle_new_weakref', 'uint', ['pointer', 'bool']);
const monoGcHandleGetTarget = native(
    'mono_gchandle_get_target', 'pointer', ['uint']);
const monoGcHandleFree = native(
    'mono_gchandle_free', 'void', ['uint']);

let active = false;
let fired = [];

function start() {
    let assemblyImage = ptr(0);
    const assemblyCallback = new NativeCallback(function (assembly, _userdata) {
        const image = monoAssemblyGetImage(assembly);
        const nameAddress = monoImageGetName(image);
        if (!nameAddress.isNull() && nameAddress.readUtf8String() === 'Assembly-CSharp') {
            assemblyImage = image;
        }
    }, 'void', ['pointer', 'pointer'], abi);
    monoAssemblyForeach(assemblyCallback, ptr(0));
    if (assemblyImage.isNull()) {
        throw new Error('Assembly-CSharp is not loaded');
    }

    const trainerClass = monoClassFromName(
        assemblyImage,
        Memory.allocUtf8String('Training'),
        Memory.allocUtf8String('TrainerMelee'));
    if (trainerClass.isNull()) {
        throw new Error('Training.TrainerMelee was not found');
    }

    const trainingClass = monoClassFromName(
        assemblyImage,
        Memory.allocUtf8String('Training'),
        Memory.allocUtf8String('TrainingMelee'));
    const projectileClass = monoClassFromName(
        assemblyImage,
        Memory.allocUtf8String('SGlibrary'),
        Memory.allocUtf8String('Projectile'));
    if (trainingClass.isNull() || projectileClass.isNull()) {
        throw new Error('TrainingMelee or Projectile was not found');
    }

    function compiledMethod(startClass, name, returnType, includeParents) {
        let currentClass = startClass;
        let method = ptr(0);
        while (!currentClass.isNull() && method.isNull()) {
            method = monoClassGetMethodFromName(
                currentClass, Memory.allocUtf8String(name), 0);
            currentClass = includeParents ? monoClassGetParent(currentClass) : ptr(0);
        }
        if (method.isNull()) {
            throw new Error(name + ' was not found');
        }
        return guarded(new NativeFunction(
            monoCompileMethod(method), returnType, ['pointer'], abi), returnType);
    }

    function fieldOffset(klass, name) {
        const field = monoClassGetFieldFromName(
            klass, Memory.allocUtf8String(name));
        if (field.isNull()) {
            throw new Error('Field not found: ' + name);
        }
        return monoFieldGetOffset(field);
    }

    // Calling these compiled property getters is read-only.  CanGetBlocked is the
    // same predicate used by TrainingMelee.Update inside the game.
    const getCanGetBlocked = compiledMethod(
        trainerClass, 'get_CanGetBlocked', 'bool', false);
    const getAttackDirection = compiledMethod(
        trainerClass, 'get_AttackDirection', 'int', false);
    const getProjectileEnabled = compiledMethod(
        projectileClass, 'get_enabled', 'bool', true);

    const projectileIdOffset = fieldOffset(
        projectileClass, '<Id>k__BackingField');
    const projectileAngleOffset = fieldOffset(
        projectileClass, '<Angle>k__BackingField');
    const projectilePositionOffset = fieldOffset(
        projectileClass, 'posPreviousFrame');
    const projectileTargetOffset = fieldOffset(projectileClass, 'target');
    const projectilePoolOffset = fieldOffset(trainingClass, 'projPool');

    const noopWorldCallback = new NativeCallback(
        function () {}, 'void', [], abi);
    let weakHandles = [];
    let trainingHandle = 0;
    let lastDiscovery = Date.now();
    let itemStates = new Map();
    let poolItemsOffset = null;
    let listArrayOffset = null;
    let listSizeOffset = null;

    function findLiveObjects(klass, maximum) {
        const objects = [];
        const objectCallback = new NativeCallback(function (array, count, _userdata) {
            for (let index = 0; index < count; ++index) {
                objects.push(array.add(index * Process.pointerSize).readPointer());
            }
        }, 'void', ['pointer', 'int', 'pointer'], abi);
        const state = monoLivenessBegin(
            klass, maximum, objectCallback, ptr(0),
            noopWorldCallback, noopWorldCallback);
        monoLivenessFromStatics(state);
        monoLivenessEnd(state);
        return objects;
    }

    function clearHandles() {
        for (const handle of weakHandles) {
            monoGcHandleFree(handle);
        }
        weakHandles = [];
        fired = [];
    }

    function discoverTrainers() {
        clearHandles();
        const objects = findLiveObjects(trainerClass, 10000);
        for (const object of objects) {
            if (!unityAlive(object)) continue;
            weakHandles.push(monoGcHandleNewWeak(object, 0));
            fired.push(false);
        }
    }

    function discoverTrainingManager() {
        if (trainingHandle !== 0) {
            monoGcHandleFree(trainingHandle);
            trainingHandle = 0;
        }
        const managers = findLiveObjects(trainingClass, 1000);
        for (const manager of managers) {
            if (unityAlive(manager)) {
                trainingHandle = monoGcHandleNewWeak(manager, 0);
                break;
            }
        }
    }

    discoverTrainers();
    discoverTrainingManager();
    send({
        kind: 'ready',
        trainers: weakHandles.length,
        projectiles: trainingHandle !== 0
    });

    function scanProjectiles() {
        if (trainingHandle === 0) {
            return;
        }
        const manager = monoGcHandleGetTarget(trainingHandle);
        if (!unityAlive(manager)) {
            return;
        }
        const pool = manager.add(projectilePoolOffset).readPointer();
        if (pool.isNull()) {
            return;
        }
        if (poolItemsOffset === null) {
            poolItemsOffset = fieldOffset(
                monoObjectGetClass(pool), '<Items>k__BackingField');
        }
        const items = pool.add(poolItemsOffset).readPointer();
        if (items.isNull()) {
            return;
        }
        if (listArrayOffset === null || listSizeOffset === null) {
            const listClass = monoObjectGetClass(items);
            listArrayOffset = fieldOffset(listClass, '_items');
            listSizeOffset = fieldOffset(listClass, '_size');
        }
        const array = items.add(listArrayOffset).readPointer();
        const size = items.add(listSizeOffset).readS32();
        if (array.isNull() || size < 0 || size > 256) {
            return;
        }

        for (let index = 0; index < size; ++index) {
            // Unity 2018 uses 64-bit MonoArray: header 16, bounds 8,
            // max_length 8, followed by references at offset 32.
            const object = array.add(32 + index * Process.pointerSize).readPointer();
            if (object.isNull()) {
                continue;
            }
            const address = object.toString();
            let state = itemStates.get(address);
            if (state === undefined) {
                state = {fired: false, distance: Infinity, step: 0};
                itemStates.set(address, state);
            }

            if (!getProjectileEnabled(object)) {
                state.fired = false;
                state.distance = Infinity;
                state.step = 0;
                continue;
            }

            const id = object.add(projectileIdOffset).readS32();
            // Apple, AppleGolden, AppleMagic, Shuriken.
            if (id !== 1 && id !== 2 && id !== 3 && id !== 7) {
                continue;
            }
            const px = object.add(projectilePositionOffset).readFloat();
            const py = object.add(projectilePositionOffset + 4).readFloat();
            const tx = object.add(projectileTargetOffset).readFloat();
            const ty = object.add(projectileTargetOffset + 4).readFloat();
            const distance = Math.hypot(px - tx, py - ty);
            if (!Number.isFinite(distance) || distance > 100) {
                continue;
            }

            if (Number.isFinite(state.distance) &&
                    Math.abs(distance - state.distance) > 0.0001) {
                const measuredStep = state.distance - distance;
                if (measuredStep > 0) {
                    state.step = measuredStep;
                }
            }
            // A pooled object can be reused without our thread observing its
            // disabled interval.  The jump back to spawn radius rearms it.
            if (state.fired && Number.isFinite(state.distance) &&
                    distance > state.distance + 2.0 && distance > 8.0) {
                state.fired = false;
                state.step = 0;
            }
            state.distance = distance;

            const angle = Math.round(
                object.add(projectileAngleOffset + 8).readFloat());
            let key = null;
            if (angle === 0 || angle === 360) {
                key = 'A';
            } else if (angle === 180 || angle === -180) {
                key = 'D';
            } else if (angle === 90 || angle === 270 || angle === -90) {
                key = 'W';
            }
            if (key === null) {
                continue;
            }

            const tolerance = id === 7 ? 0.93 : (id === 2 ? 100.0 : 0.88);
            const hitableDistance = id === 7 ? 3.7 : (id === 2 ? 4.0 : 3.6);
            const idealOuterEdge = Math.min(
                hitableDistance - 0.03, 2.65 + tolerance - 0.03);
            // Input is consumed by Unity on the following game frame.  Retaining
            // the last distinct per-frame movement makes this prediction follow
            // every speed increase without using a fixed millisecond delay.
            const nextFrameDistance = distance - state.step;
            if (active && !state.fired &&
                    (distance <= idealOuterEdge || nextFrameDistance <= idealOuterEdge)) {
                state.fired = true;
                send({
                    kind: 'item',
                    key: key,
                    id: id,
                    distance: distance,
                    predicted: nextFrameDistance
                });
            }
        }
    }

    function scan() {
        let liveCount = 0;
        for (let index = 0; index < weakHandles.length; ++index) {
            const object = monoGcHandleGetTarget(weakHandles[index]);
            if (!unityAlive(object)) {
                fired[index] = false;
                continue;
            }
            ++liveCount;
            const canBlock = getCanGetBlocked(object);
            if (!active) {
                fired[index] = false;
            } else if (canBlock && !fired[index]) {
                fired[index] = true;
                send({
                    kind: 'parry',
                    direction: getAttackDirection(object)
                });
            } else if (!canBlock) {
                fired[index] = false;
            }
        }
        // Searching the heap is expensive and, under Wine, runs on the game's
        // own thread, so it is retried only twice per second.
        if (liveCount === 0 && Date.now() - lastDiscovery >= 500) {
            lastDiscovery = Date.now();
            discoverTrainers();
            discoverTrainingManager();
        }
        scanProjectiles();
    }
    return scan;
}

runOnMonoThread(start, 1);

rpc.exports = {
    setActive(value) {
        active = Boolean(value);
        if (!active) {
            fired.fill(false);
        }
        return active;
    }
};
"""


class MannequinMemoryReader:
    """Emit A/W/D for exact mannequin and projectile memory events."""

    _DIRECTION_KEYS = {0: "A", 1: "W", 2: "D"}

    def __init__(
        self,
        pid: int,
        on_parry: Callable[[str], None],
        on_item: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] = print,
    ) -> None:
        self._on_parry = on_parry
        self._on_item = on_item
        self._on_status = on_status
        self._lock = threading.Lock()
        self._closed = False
        # Under Wine this also makes the process safe for Frida to attach.
        source = agent_source(pid, _AGENT_SOURCE)
        self._session = frida.attach(pid)
        self._script = self._session.create_script(source)
        self._script.on("message", self._handle_message)
        self._script.load()

    def _handle_message(self, message: dict, _data: bytes | None) -> None:
        if message.get("type") == "error":
            description = message.get("description", "unknown Frida agent error")
            self._on_status(f"Memory reader error: {description}")
            return
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return
        kind = payload.get("kind")
        if kind == "ready":
            self._on_status(
                "Memory reader ready: "
                f"{int(payload.get('trainers', 0))} mannequins, "
                f"projectiles={'yes' if payload.get('projectiles') else 'no'}."
            )
        elif kind == "runtimeError":
            self._on_status(f"Memory reader error: {payload.get('message')}")
        elif kind == "parry":
            key = self._DIRECTION_KEYS.get(payload.get("direction"))
            if key is not None:
                self._on_parry(key)
        elif kind == "item" and self._on_item is not None:
            key = payload.get("key")
            if key in self._DIRECTION_KEYS.values():
                self._on_item(key)

    def set_active(self, active: bool) -> None:
        with self._lock:
            if not self._closed:
                self._script.exports_sync.set_active(bool(active))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._script.unload()
            finally:
                self._session.detach()

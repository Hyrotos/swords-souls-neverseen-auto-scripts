"""Unity/Mono automation for the magic training targets."""

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
const classGetParent = native('mono_class_get_parent', 'pointer', ['pointer']);
const classGetName = native('mono_class_get_name', 'pointer', ['pointer']);
const objectGetClass = native('mono_object_get_class', 'pointer', ['pointer']);
const classGetField = native('mono_class_get_field_from_name', 'pointer',
    ['pointer', 'pointer']);
const fieldGetOffset = native('mono_field_get_offset', 'int', ['pointer']);
const classGetMethod = native('mono_class_get_method_from_name', 'pointer',
    ['pointer', 'pointer', 'int']);
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

function start() {
    const images = {};
    const assemblyCallback = new NativeCallback(function (assembly, _user) {
        const image = assemblyGetImage(assembly);
        const name = imageGetName(image);
        if (!name.isNull()) images[name.readUtf8String()] = image;
    }, 'void', ['pointer', 'pointer'], abi);
    assemblyForeach(assemblyCallback, ptr(0));

    function string(value) { return Memory.allocUtf8String(value); }
    function klass(imageName, namespaceName, className) {
        const image = images[imageName];
        if (image === undefined) throw new Error('Image missing: ' + imageName);
        const result = classFromName(image, string(namespaceName), string(className));
        if (result.isNull())
            throw new Error('Class missing: ' + namespaceName + '.' + className);
        return result;
    }
    function fieldOffset(classAddress, fieldName) {
        const field = classGetField(classAddress, string(fieldName));
        if (field.isNull()) throw new Error('Field missing: ' + fieldName);
        return fieldGetOffset(field);
    }
    function findMethod(classAddress, methodName, argCount, parents) {
        let current = classAddress;
        let result = ptr(0);
        while (!current.isNull() && result.isNull()) {
            result = classGetMethod(current, string(methodName), argCount);
            current = parents ? classGetParent(current) : ptr(0);
        }
        if (result.isNull()) throw new Error('Method missing: ' + methodName);
        return result;
    }
    function compiled(classAddress, methodName, argCount, result, args, parents) {
        const method = new NativeFunction(
            compileMethod(findMethod(classAddress, methodName, argCount, parents)),
            result, args, abi);
        return args.length === 0 ? method : guarded(method, result);
    }

    const trainingClass = klass('Assembly-CSharp', 'Training', 'TrainingMagic');
    const targetClass = klass('Assembly-CSharp', 'Training', 'TargetBase');
    const ballClass = klass('Assembly-CSharp', 'Training', 'SoulceryBall');
    const componentClass = klass('UnityEngine.CoreModule', 'UnityEngine', 'Component');
    const transformClass = klass('UnityEngine.CoreModule', 'UnityEngine', 'Transform');
    const cameraClass = klass('UnityEngine.CoreModule', 'UnityEngine', 'Camera');

    const getActiveAndEnabled = compiled(targetClass, 'get_isActiveAndEnabled', 0,
        'bool', ['pointer'], true);
    const setEnabled = compiled(targetClass, 'set_enabled', 1,
        'void', ['pointer', 'bool'], true);
    const targetGetScored = compiled(targetClass, 'GetScored', 1,
        'void', ['pointer', 'bool'], false);
    const targetSetColliders = compiled(targetClass, 'setColliders', 1,
        'void', ['pointer', 'bool'], false);
    const targetSleep = compiled(targetClass, 'Sleep', 0,
        'void', ['pointer'], false);
    const getTransform = compiled(componentClass, 'get_transform', 0,
        'pointer', ['pointer'], false);
    const getPosition = compiled(transformClass, 'get_position_Injected', 1,
        'void', ['pointer', 'pointer'], false);
    const getMainCamera = compiled(cameraClass, 'get_main', 0,
        'pointer', [], false);
    const worldToScreen = compiled(cameraClass, 'WorldToScreenPoint_Injected', 3,
        'void', ['pointer', 'pointer', 'int', 'pointer'], false);

    const poolOffsets = [
        fieldOffset(trainingClass, 'stillPool'),
        fieldOffset(trainingClass, 'fillPool'),
        fieldOffset(trainingClass, 'starPool'),
        fieldOffset(trainingClass, 'gravityPool'),
        fieldOffset(trainingClass, 'chaserPool'),
        fieldOffset(trainingClass, 'ringPool')
    ];
    const missedOffset = fieldOffset(targetClass, 'missed');
    const starHitOffset = fieldOffset(targetClass, 'starHit');

    let trainingHandle = 0;
    let lastDiscovery = 0;
    let poolItemsOffset = null;
    let listArrayOffset = null;
    let listSizeOffset = null;
    let lastAimSend = 0;
    const targetFirstSeen = new Map();

    const noop = new NativeCallback(function () {}, 'void', [], abi);

    function classNameOf(object) {
        const address = classGetName(objectGetClass(object));
        return address.isNull() ? '?' : address.readUtf8String();
    }

    function positionOf(object) {
        const transform = getTransform(object);
        if (transform.isNull()) return null;
        const result = Memory.alloc(16);
        getPosition(transform, result);
        const value = {
            x: result.readFloat(),
            y: result.add(4).readFloat(),
            z: result.add(8).readFloat()
        };
        if (!Number.isFinite(value.x) || !Number.isFinite(value.y)) return null;
        return value;
    }

    function poolObjects(pool) {
        if (pool.isNull()) return [];
        if (poolItemsOffset === null)
            poolItemsOffset = fieldOffset(
                objectGetClass(pool), '<Items>k__BackingField');
        const list = pool.add(poolItemsOffset).readPointer();
        if (list.isNull()) return [];
        if (listArrayOffset === null) {
            const listClass = objectGetClass(list);
            listArrayOffset = fieldOffset(listClass, '_items');
            listSizeOffset = fieldOffset(listClass, '_size');
        }
        const array = list.add(listArrayOffset).readPointer();
        const size = list.add(listSizeOffset).readS32();
        if (array.isNull() || size < 0 || size > 256) return [];
        const objects = [];
        for (let index = 0; index < size; ++index) {
            const object = array.add(
                32 + index * Process.pointerSize).readPointer();
            if (!object.isNull()) objects.push(object);
        }
        return objects;
    }

    function activeTargets(manager) {
        const result = [];
        for (const offset of poolOffsets) {
            const pool = manager.add(offset).readPointer();
            for (const target of poolObjects(pool)) {
                let enabled = false;
                try { enabled = Boolean(getActiveAndEnabled(target)); }
                catch (_) { continue; }
                if (!enabled || Boolean(target.add(missedOffset).readU8())) continue;
                const position = positionOf(target);
                if (position === null) continue;
                result.push({
                    pointer: target,
                    key: target.toString(),
                    className: classNameOf(target),
                    position: position
                });
            }
        }
        return result;
    }

    function discoverTraining() {
        if (trainingHandle !== 0) {
            handleFree(trainingHandle);
            trainingHandle = 0;
        }
        const found = [];
        const callback = new NativeCallback(function (array, count, _user) {
            for (let index = 0; index < count; ++index)
                found.push(array.add(index * Process.pointerSize).readPointer());
        }, 'void', ['pointer', 'int', 'pointer'], abi);
        const state = liveBegin(trainingClass, 1000, callback, ptr(0), noop, noop);
        liveStatics(state);
        liveEnd(state);
        for (const candidate of found) {
            try {
                if (Boolean(getActiveAndEnabled(candidate))) {
                    trainingHandle = handleNew(candidate, 0);
                    break;
                }
            } catch (_) {}
        }
        if (trainingHandle === 0 && found.length !== 0)
            trainingHandle = handleNew(found[0], 0);
        lastDiscovery = Date.now();
        send({kind: 'ready', training: trainingHandle !== 0});
    }

    function managerPointer() {
        if (trainingHandle === 0) return ptr(0);
        const manager = handleTarget(trainingHandle);
        if (!unityAlive(manager)) {
            trainingHandle = 0;
            return ptr(0);
        }
        try {
            if (!Boolean(getActiveAndEnabled(manager))) return ptr(0);
        } catch (_) { return ptr(0); }
        return manager;
    }

    function scoreEveryTarget(manager) {
        const targets = activeTargets(manager);
        const now = Date.now();
        const liveKeys = new Set(targets.map(function (target) {
            return target.key;
        }));
        for (const key of Array.from(targetFirstSeen.keys())) {
            if (!liveKeys.has(key)) targetFirstSeen.delete(key);
        }
        let scored = 0;
        const types = [];
        for (const target of targets) {
            if (!targetFirstSeen.has(target.key)) {
                targetFirstSeen.set(target.key, now);
                continue;
            }
            // Give the Python side enough time to move the visible cursor to the
            // freshly spawned target before completing it through memory.
            if (now - targetFirstSeen.get(target.key) < 50) continue;
            try {
                // This is the complete successful-target chain used by the game:
                // mark a star, make the object non-colliding, notify TrainingMagic
                // (which removes it from the wave), then return it to its pool.
                target.pointer.add(starHitOffset).writeU8(1);
                setEnabled(target.pointer, 0);
                targetSetColliders(target.pointer, 0);
                targetGetScored(target.pointer, 1);
                targetSleep(target.pointer);
                scored += 1;
                types.push(target.className);
            } catch (error) {
                send({kind: 'runtimeError', message: String(error)});
            }
        }
        if (scored !== 0) send({kind: 'scored', count: scored, types: types});
    }

    function scan() {
        if (trainingHandle === 0) {
            if (Date.now() - lastDiscovery > 500) discoverTraining();
            return;
        }
        const manager = managerPointer();
        if (manager.isNull()) {
            if (Date.now() - lastDiscovery > 500) discoverTraining();
            return;
        }
        if (!active) return;
        const targets = activeTargets(manager);
        if (targets.length === 0) return;

        // Prefer the collectible star for the visible cursor movement. Direct
        // scoring still completes every active object in the same game frame.
        let target = targets[0];
        for (const candidate of targets) {
            if (candidate.className === 'TargetStar') {
                target = candidate;
                break;
            }
        }
        const camera = getMainCamera();
        if (camera.isNull()) return;
        const world = Memory.alloc(16);
        world.writeFloat(target.position.x);
        world.add(4).writeFloat(target.position.y);
        world.add(8).writeFloat(target.position.z);
        const screen = Memory.alloc(16);
        worldToScreen(camera, world, 0, screen);
        const screenX = screen.readFloat();
        const screenY = screen.add(4).readFloat();
        if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return;
        const now = Date.now();
        if (now - lastAimSend >= 8) {
            lastAimSend = now;
            send({
                kind: 'aim',
                className: target.className,
                screenX: screenX,
                screenY: screenY
            });
        }
    }

    // SoulceryBall.Update always runs on Unity's main thread during magic
    // training. Managed scoring is performed here so Unity objects and wave events
    // are never mutated from Frida's timer thread.
    Interceptor.attach(compileMethod(findMethod(ballClass, 'Update', 0, false)), {
        onEnter(_args) {
            if (!active) return;
            const manager = managerPointer();
            if (!manager.isNull()) scoreEveryTarget(manager);
        }
    });

    discoverTraining();
    return scan;
}

runOnMonoThread(start, 8);

rpc.exports = {
    setActive(value) {
        active = Boolean(value);
        return active;
    }
};
"""


class MagicMemoryReader:
    def __init__(
        self,
        pid: int,
        on_aim: Callable[[dict], None],
        on_scored: Callable[[dict], None],
        on_status: Callable[[str], None] = print,
    ) -> None:
        self._on_aim = on_aim
        self._on_scored = on_scored
        self._on_status = on_status
        self._errors = AgentErrors(on_status)
        self._lock = threading.Lock()
        self._closed = False
        # Under Wine this also makes the process safe for Frida to attach.
        source = agent_source(pid, _AGENT_SOURCE)
        self._session = frida.attach(pid)
        self._script = self._session.create_script(source, name="magic")
        self._script.on("message", self._handle_message)
        self._script.load()

    def _handle_message(self, message: dict, _data: bytes | None) -> None:
        if message.get("type") != "send":
            self._errors.report(message)
            return
        payload = message.get("payload", {})
        kind = payload.get("kind")
        if kind == "aim":
            self._on_aim(payload)
        elif kind == "scored":
            self._on_scored(payload)
        elif kind == "ready":
            state = "yes" if payload.get("training") else "no"
            self._on_status(f"Magic memory ready: training={state}.")
        elif kind == "runtimeError":
            self._on_status(f"Memory runtime error: {payload.get('message')}")

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

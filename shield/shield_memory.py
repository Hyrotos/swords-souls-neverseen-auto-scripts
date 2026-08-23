"""Unity/Mono helper for defense aiming and perfect-score rewards."""

from __future__ import annotations

from collections.abc import Callable
import threading

import frida


_AGENT_SOURCE = r"""
const mono = Process.getModuleByName('mono.dll');

function exported(name) {
    const address = mono.findExportByName(name);
    if (address === null) throw new Error('Mono export not found: ' + name);
    return address;
}

function native(name, result, args) {
    return new NativeFunction(exported(name), result, args);
}

const monoGetRootDomain = native('mono_get_root_domain', 'pointer', []);
const monoThreadAttach = native('mono_thread_attach', 'pointer', ['pointer']);
const monoAssemblyForeach = native(
    'mono_assembly_foreach', 'void', ['pointer', 'pointer']);
const monoAssemblyGetImage = native(
    'mono_assembly_get_image', 'pointer', ['pointer']);
const monoImageGetName = native(
    'mono_image_get_name', 'pointer', ['pointer']);
const monoClassFromName = native(
    'mono_class_from_name', 'pointer', ['pointer', 'pointer', 'pointer']);
const monoClassGetParent = native(
    'mono_class_get_parent', 'pointer', ['pointer']);
const monoClassGetMethodFromName = native(
    'mono_class_get_method_from_name', 'pointer',
    ['pointer', 'pointer', 'int']);
const monoCompileMethod = native(
    'mono_compile_method', 'pointer', ['pointer']);
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
    'mono_unity_liveness_calculation_from_statics', 'void', ['pointer']);
const monoLivenessEnd = native(
    'mono_unity_liveness_calculation_end', 'void', ['pointer']);
const monoGcHandleNewWeak = native(
    'mono_gchandle_new_weakref', 'uint', ['pointer', 'bool']);
const monoGcHandleGetTarget = native(
    'mono_gchandle_get_target', 'pointer', ['uint']);
const monoGcHandleFree = native(
    'mono_gchandle_free', 'void', ['uint']);

monoThreadAttach(monoGetRootDomain());

const images = {};
const assemblyCallback = new NativeCallback(function (assembly, _userdata) {
    const image = monoAssemblyGetImage(assembly);
    const nameAddress = monoImageGetName(image);
    if (!nameAddress.isNull()) {
        images[nameAddress.readUtf8String()] = image;
    }
}, 'void', ['pointer', 'pointer']);
monoAssemblyForeach(assemblyCallback, ptr(0));

function klass(imageName, namespaceName, className) {
    const image = images[imageName];
    if (image === undefined) throw new Error('Image not loaded: ' + imageName);
    const result = monoClassFromName(
        image,
        Memory.allocUtf8String(namespaceName),
        Memory.allocUtf8String(className));
    if (result.isNull()) throw new Error('Class not found: ' + className);
    return result;
}

function fieldOffset(classAddress, fieldName) {
    const field = monoClassGetFieldFromName(
        classAddress, Memory.allocUtf8String(fieldName));
    if (field.isNull()) throw new Error('Field not found: ' + fieldName);
    return monoFieldGetOffset(field);
}

function compiledMethod(classAddress, methodName, result, parents) {
    let current = classAddress;
    let method = ptr(0);
    while (!current.isNull() && method.isNull()) {
        method = monoClassGetMethodFromName(
            current, Memory.allocUtf8String(methodName), 0);
        current = parents ? monoClassGetParent(current) : ptr(0);
    }
    if (method.isNull()) throw new Error('Method not found: ' + methodName);
    return new NativeFunction(monoCompileMethod(method), result, ['pointer']);
}

function compiledBoolSetter(classAddress, methodName, parents) {
    let current = classAddress;
    let method = ptr(0);
    while (!current.isNull() && method.isNull()) {
        method = monoClassGetMethodFromName(
            current, Memory.allocUtf8String(methodName), 1);
        current = parents ? monoClassGetParent(current) : ptr(0);
    }
    if (method.isNull()) throw new Error('Method not found: ' + methodName);
    return new NativeFunction(
        monoCompileMethod(method), 'void', ['pointer', 'bool']);
}

function methodAddress(classAddress, methodName, argCount, parents) {
    let current = classAddress;
    let method = ptr(0);
    while (!current.isNull() && method.isNull()) {
        method = monoClassGetMethodFromName(
            current, Memory.allocUtf8String(methodName), argCount);
        current = parents ? monoClassGetParent(current) : ptr(0);
    }
    if (method.isNull()) throw new Error('Method not found: ' + methodName);
    return monoCompileMethod(method);
}

const trainingClass = klass(
    'Assembly-CSharp', 'Training', 'TrainingDefense');
const projectileClass = klass(
    'Assembly-CSharp', 'SGlibrary', 'Projectile');
const componentClass = klass(
    'UnityEngine.CoreModule', 'UnityEngine', 'Component');
const transformClass = klass(
    'UnityEngine.CoreModule', 'UnityEngine', 'Transform');

const getEnabled = compiledMethod(
    projectileClass, 'get_enabled', 'bool', true);
const setEnabled = compiledBoolSetter(
    projectileClass, 'set_enabled', true);
const getTransform = compiledMethod(
    componentClass, 'get_transform', 'pointer', false);

function compiledInjected(methodName) {
    const method = monoClassGetMethodFromName(
        transformClass, Memory.allocUtf8String(methodName), 1);
    if (method.isNull()) throw new Error('Transform method missing: ' + methodName);
    return new NativeFunction(
        monoCompileMethod(method), 'void', ['pointer', 'pointer']);
}

const getPosition = compiledInjected('get_position_Injected');
const getLocalScale = compiledInjected('get_localScale_Injected');

const heroOffset = fieldOffset(trainingClass, 'heroDefense');
const poolOffset = fieldOffset(trainingClass, 'projPool');
const pointerOffset = fieldOffset(
    klass('Assembly-CSharp', 'Hero', 'HeroDefense'), 'pointer');
const idOffset = fieldOffset(projectileClass, '<Id>k__BackingField');
const speedOffset = fieldOffset(projectileClass, '<Speed>k__BackingField');
const positionOffset = fieldOffset(projectileClass, 'posPreviousFrame');
const colliderOffset = fieldOffset(
    projectileClass, '<Collider>k__BackingField');

const noopWorldCallback = new NativeCallback(function () {}, 'void', []);
let trainingHandle = 0;
let active = false;
let poolItemsOffset = null;
let listArrayOffset = null;
let listSizeOffset = null;
let lastDiscovery = 0;
let lastSend = 0;
let awardedStars = 0;

// A successful shield block normally calls TrainingBase.Score(false, ...).
// Switching only that flag to true uses the game's existing perfect-score
// path: the same single block awards one star without a second score/combo.
// Misses do not call this success path, while collectible stars already pass
// true and therefore remain unchanged.
const trainingBaseClass = monoClassGetParent(trainingClass);
const scoreAddress = methodAddress(trainingBaseClass, 'Score', 3, true);
Interceptor.attach(scoreAddress, {
    onEnter(args) {
        if (!active || trainingHandle === 0) return;
        const manager = monoGcHandleGetTarget(trainingHandle);
        if (manager.isNull() || !args[0].equals(manager)) return;
        if (args[1].toInt32() !== 0) return;
        args[1] = ptr(1);
        awardedStars += 1;
        send({kind: 'starAwarded', total: awardedStars});
    }
});

function findTraining() {
    if (trainingHandle !== 0) {
        monoGcHandleFree(trainingHandle);
        trainingHandle = 0;
    }
    const objects = [];
    const callback = new NativeCallback(function (array, count, _userdata) {
        for (let index = 0; index < count; ++index) {
            objects.push(array.add(index * Process.pointerSize).readPointer());
        }
    }, 'void', ['pointer', 'int', 'pointer']);
    const state = monoLivenessBegin(
        trainingClass, 1000, callback, ptr(0),
        noopWorldCallback, noopWorldCallback);
    monoLivenessFromStatics(state);
    monoLivenessEnd(state);
    if (objects.length !== 0) {
        trainingHandle = monoGcHandleNewWeak(objects[0], 0);
    }
    lastDiscovery = Date.now();
    send({kind: 'ready', training: trainingHandle !== 0});
}

function vector3(component, getter) {
    const transform = getTransform(component);
    if (transform.isNull()) return null;
    const output = Memory.alloc(16);
    getter(transform, output);
    return {
        x: output.readFloat(),
        y: output.add(4).readFloat(),
        z: output.add(8).readFloat()
    };
}

function projectileList(manager) {
    const pool = manager.add(poolOffset).readPointer();
    if (pool.isNull()) return null;
    if (poolItemsOffset === null) {
        poolItemsOffset = fieldOffset(
            monoObjectGetClass(pool), '<Items>k__BackingField');
    }
    const list = pool.add(poolItemsOffset).readPointer();
    if (list.isNull()) return null;
    if (listArrayOffset === null) {
        const listClass = monoObjectGetClass(list);
        listArrayOffset = fieldOffset(listClass, '_items');
        listSizeOffset = fieldOffset(listClass, '_size');
    }
    const array = list.add(listArrayOffset).readPointer();
    const size = list.add(listSizeOffset).readS32();
    if (array.isNull() || size < 0 || size > 512) return null;
    return {array: array, size: size};
}

function scan() {
    if (trainingHandle === 0) {
        if (Date.now() - lastDiscovery > 1000) findTraining();
        return;
    }
    const manager = monoGcHandleGetTarget(trainingHandle);
    if (manager.isNull()) {
        trainingHandle = 0;
        return;
    }
    const hero = manager.add(heroOffset).readPointer();
    if (hero.isNull()) return;
    const pointerObject = hero.add(pointerOffset).readPointer();
    if (pointerObject.isNull()) return;

    const heroPosition = vector3(hero, getPosition);
    const heroScale = vector3(hero, getLocalScale);
    const pointerPosition = vector3(pointerObject, getPosition);
    if (heroPosition === null || heroScale === null || pointerPosition === null) {
        return;
    }
    const center = {
        x: heroPosition.x,
        y: heroPosition.y + 2.34 * heroScale.y
    };

    const list = projectileList(manager);
    if (list === null) return;
    let danger = null;
    for (let index = 0; index < list.size; ++index) {
        // 64-bit Unity 2018 MonoArray data starts at offset 32.
        const projectile = list.array.add(
            32 + index * Process.pointerSize).readPointer();
        if (projectile.isNull() || !getEnabled(projectile)) continue;
        const id = projectile.add(idOffset).readS32();
        if (id <= 0) continue;
        if (id === 8) {
            // Stars must reach HeroDefense so the game can award them, but
            // they must never enter ShieldDefense.OnTriggerEnter2D. Disabling
            // only the star's Collider2D achieves exactly that. Projectile's
            // coordinate-based hero collision remains active, and Reset()
            // re-enables this pooled collider before the next projectile use.
            const collider = projectile.add(colliderOffset).readPointer();
            if (!collider.isNull() && getEnabled(collider)) {
                setEnabled(collider, 0);
            }
            continue;
        }
        const x = projectile.add(positionOffset).readFloat();
        const y = projectile.add(positionOffset + 4).readFloat();
        const dx = x - center.x;
        const dy = y - center.y;
        const distance = Math.hypot(dx, dy);
        if (!Number.isFinite(distance) || distance < 0.05 || distance > 30) {
            continue;
        }
        const speed = Math.max(0.1, projectile.add(speedOffset).readFloat());
        const info = {
            id: id,
            x: x,
            y: y,
            dx: dx,
            dy: dy,
            distance: distance,
            speed: speed,
            eta: distance / speed
        };
        if (danger === null || info.eta < danger.eta) {
            danger = info;
        }
    }

    if (!active || danger === null) return;
    const selected = danger;
    const mode = 'block';
    const aimX = danger.dx / danger.distance;
    const aimY = danger.dy / danger.distance;

    const radius = 3.35;
    const targetX = center.x + radius * aimX;
    const targetY = center.y + radius * aimY;
    const now = Date.now();
    if (now - lastSend >= 4) {
        lastSend = now;
        send({
            kind: 'aim',
            mode: mode,
            id: selected.id,
            distance: selected.distance,
            targetX: targetX,
            targetY: targetY,
            currentX: pointerPosition.x,
            currentY: pointerPosition.y,
            centerX: center.x,
            centerY: center.y
        });
    }
}

findTraining();
setInterval(scan, 2);

rpc.exports = {
    setActive(value) {
        active = Boolean(value);
        return active;
    }
};
"""


class ShieldMemoryReader:
    def __init__(
        self,
        pid: int,
        on_aim: Callable[[dict], None],
        on_status: Callable[[str], None] = print,
    ) -> None:
        self._on_aim = on_aim
        self._on_status = on_status
        self._lock = threading.Lock()
        self._closed = False
        self._session = frida.attach(pid)
        self._script = self._session.create_script(_AGENT_SOURCE)
        self._script.on("message", self._handle_message)
        self._script.load()

    def _handle_message(self, message: dict, _data: bytes | None) -> None:
        if message.get("type") == "error":
            self._on_status(
                f"Memory reader error: {message.get('description', 'unknown error')}"
            )
            return
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return
        if payload.get("kind") == "ready":
            state = "yes" if payload.get("training") else "no"
            self._on_status(f"Defense memory reader ready: training={state}.")
        elif payload.get("kind") == "aim":
            self._on_aim(payload)
        elif payload.get("kind") == "starAwarded":
            total = int(payload.get("total", 0))
            self._on_status(f"Perfect block: star awarded (total={total}).")

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

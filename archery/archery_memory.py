"""Unity/Mono target tracker and in-flight arrow guidance for archery training."""

from __future__ import annotations

from collections.abc import Callable
import threading

import frida

from wine_frida import AgentErrors, agent_source


_AGENT_SOURCE = r"""
const assemblyForeach = native('mono_assembly_foreach', 'void', ['pointer', 'pointer']);
const assemblyGetImage = native('mono_assembly_get_image', 'pointer', ['pointer']);
const imageGetName = native('mono_image_get_name', 'pointer', ['pointer']);
const classFromName = native('mono_class_from_name', 'pointer', ['pointer', 'pointer', 'pointer']);
const classGetParent = native('mono_class_get_parent', 'pointer', ['pointer']);
const classGetName = native('mono_class_get_name', 'pointer', ['pointer']);
const objectGetClass = native('mono_object_get_class', 'pointer', ['pointer']);
const classGetField = native('mono_class_get_field_from_name', 'pointer', ['pointer', 'pointer']);
const fieldGetOffset = native('mono_field_get_offset', 'int', ['pointer']);
const classGetMethod = native('mono_class_get_method_from_name', 'pointer', ['pointer', 'pointer', 'int']);
const compileMethod = native('mono_compile_method', 'pointer', ['pointer']);
const liveBegin = native('mono_unity_liveness_calculation_begin', 'pointer',
    ['pointer', 'uint', 'pointer', 'pointer', 'pointer', 'pointer']);
const liveStatics = native('mono_unity_liveness_calculation_from_statics', 'void', ['pointer']);
const liveEnd = native('mono_unity_liveness_calculation_end', 'void', ['pointer']);
const handleNew = native('mono_gchandle_new_weakref', 'uint', ['pointer', 'bool']);
const handleTarget = native('mono_gchandle_get_target', 'pointer', ['uint']);
const handleFree = native('mono_gchandle_free', 'void', ['uint']);

let active = false;
let guideArrows = false;

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
        if (result.isNull()) throw new Error('Class missing: ' + namespaceName + '.' + className);
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

    const rangeClass = klass('Assembly-CSharp', 'Training', 'TrainingRange');
    const targetClass = klass('Assembly-CSharp', 'Training', 'TargetBase');
    const heroBowClass = klass('Assembly-CSharp', 'Hero', 'HeroBow');
    const arrowClass = klass('Assembly-CSharp', 'Training', 'Arrow');
    const animationClass = klass('Assembly-CSharp', 'SGlibrary', 'SGAnimation');
    const depthClass = klass('Assembly-CSharp', 'SGlibrary', 'Depth');
    const componentClass = klass('UnityEngine.CoreModule', 'UnityEngine', 'Component');
    const transformClass = klass('UnityEngine.CoreModule', 'UnityEngine', 'Transform');
    const cameraClass = klass('UnityEngine.CoreModule', 'UnityEngine', 'Camera');

    const getEnabled = compiled(targetClass, 'get_enabled', 0, 'bool', ['pointer'], true);
    const getActiveAndEnabled = compiled(targetClass, 'get_isActiveAndEnabled', 0,
        'bool', ['pointer'], true);
    const setEnabled = compiled(targetClass, 'set_enabled', 1,
        'void', ['pointer', 'bool'], true);
    const getTransform = compiled(componentClass, 'get_transform', 0, 'pointer', ['pointer'], false);
    const getPosition = compiled(transformClass, 'get_position_Injected', 1,
        'void', ['pointer', 'pointer'], false);
    const getMainCamera = compiled(cameraClass, 'get_main', 0, 'pointer', [], false);
    const worldToScreen = compiled(cameraClass, 'WorldToScreenPoint_Injected', 3,
        'void', ['pointer', 'pointer', 'int', 'pointer'], false);
    const isAnimationPlaying = compiled(animationClass, 'IsPlaying', 2,
        'bool', ['pointer', 'pointer', 'float'], false);
    const getDepthLevel = compiled(depthClass, 'get_Level', 0,
        'float', ['pointer'], false);
    const targetGetScored = compiled(targetClass, 'GetScored', 1,
        'void', ['pointer', 'bool'], false);
    const targetSetColliders = compiled(targetClass, 'setColliders', 1,
        'void', ['pointer', 'bool'], false);
    const targetSleep = compiled(targetClass, 'Sleep', 0,
        'void', ['pointer'], false);

    const heroBowOffset = fieldOffset(rangeClass, 'heroBow');
    const coinPoolOffset = fieldOffset(rangeClass, 'coinPool');
    const poolOffsets = [
        coinPoolOffset,
        fieldOffset(rangeClass, 'dummyPool'),
        fieldOffset(rangeClass, 'groundPool'),
        fieldOffset(rangeClass, 'extentPool'),
        fieldOffset(rangeClass, 'rotatePool'),
        fieldOffset(rangeClass, 'rollPool')
    ];
    const arrowPoolOffset = fieldOffset(heroBowClass, '<ArrowPool>k__BackingField');
    const bowAnimationOffset = fieldOffset(heroBowClass, 'anim');
    const bowCanShootOffset = fieldOffset(heroBowClass, 'canShoot');
    const bowMustAutoShootOffset = fieldOffset(heroBowClass, 'mustAutoShoot');
    const targetSpriteOffset = fieldOffset(targetClass, 'targetSprite');
    const spritePivotOffset = fieldOffset(targetClass, 'spritePivot');
    const missedOffset = fieldOffset(targetClass, 'missed');
    const starHitOffset = fieldOffset(targetClass, 'starHit');
    const scaleOffset = fieldOffset(targetClass, 'scale');
    const collidersOffset = fieldOffset(targetClass, 'colliders');
    const targetDepthOffset = fieldOffset(targetClass, '<Depth>k__BackingField');
    const arrowCollidedOffset = fieldOffset(arrowClass, 'collided');
    const arrowCrosshairOffset = fieldOffset(arrowClass, 'crosshairPosition');
    const arrowDepthOffset = fieldOffset(arrowClass, 'depth');

    let rangeHandle = 0;
    let lastDiscovery = 0;
    let poolItemsOffset = null;
    let listArrayOffset = null;
    let listSizeOffset = null;
    let lastSend = 0;
    let previousTargetKeys = new Set();
    const targetBirth = new Map();
    let previousArrowKeys = new Set();
    const arrowAssignments = new Map();
    const disabledColliders = new Map();
    const forcedTargetScores = new Set();
    let shotSequence = 0;
    let pendingTarget = null;
    let currentTargetsForShot = [];
    const noop = new NativeCallback(function () {}, 'void', [], abi);
    const loadAnimationName = string('load');
    const holdAnimationName = string('idle3D');
    const shootAnimationName = string('shoot');

    function position(transform) {
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

    function centerOf(target, className) {
        const root = position(getTransform(target));
        if (root === null) return null;

        // This is the exact formula used by TargetDummy.GetStarPos().
        if (className === 'TargetDummy') {
            return {
                x: root.x,
                y: root.y + 6.5 * target.add(scaleOffset).readFloat(),
                z: root.z
            };
        }

        // TargetGround.GetStarPos() returns targetSprite.transform.position.
        // Extent, Roll and Rotate all inherit TargetGround, so this is also their
        // exact moving star position (not the root or the extension arm).
        const sprite = target.add(targetSpriteOffset).readPointer();
        if (!sprite.isNull()) {
            const spritePosition = position(getTransform(sprite));
            if (spritePosition !== null) return spritePosition;
        }
        const pivot = target.add(spritePivotOffset).readPointer();
        if (!pivot.isNull()) {
            const pivotPosition = position(pivot);
            if (pivotPosition !== null) return pivotPosition;
        }
        return root;
    }

    function depthLevel(object, offset) {
        try {
            const depth = object.add(offset).readPointer();
            if (depth.isNull()) return Number.POSITIVE_INFINITY;
            const level = Number(getDepthLevel(depth));
            return Number.isFinite(level) ? level : Number.POSITIVE_INFINITY;
        } catch (_) {
            return Number.POSITIVE_INFINITY;
        }
    }

    function writeArrowAim(arrow, point) {
        if (point === null || point === undefined) return;
        arrow.add(arrowCrosshairOffset).writeFloat(point.x);
        arrow.add(arrowCrosshairOffset + 4).writeFloat(point.y);
        arrow.add(arrowCrosshairOffset + 8).writeFloat(point.z);
    }

    function colliderClassName(collider) {
        const address = classGetName(objectGetClass(collider));
        return address.isNull() ? '?' : address.readUtf8String();
    }

    function scoreGuaranteedShotTargets() {
        if (!active || !guideArrows) return;
        // TargetBase.GetHit only disables the target. Normally Arrow.hitCommon
        // later calls GetScored for objects stored in Arrow.targetsHit. Execute the
        // complete star-scoring path for every target that existed when this arrow
        // was released, including TargetCoin.
        const batch = currentTargetsForShot.slice();
        for (const target of batch) {
            if (forcedTargetScores.has(target.key)) continue;
            let enabled = false;
            try { enabled = Boolean(getActiveAndEnabled(target.pointer)); }
            catch (_) { continue; }
            if (!enabled) continue;
            forcedTargetScores.add(target.key);
            try {
                target.pointer.add(starHitOffset).writeU8(1);
                setEnabled(target.pointer, 0);
                targetSetColliders(target.pointer, 0);
                targetGetScored(target.pointer, 1);
                targetSleep(target.pointer);
            } catch (error) {
                forcedTargetScores.delete(target.key);
                send({kind: 'runtimeError', message: String(error)});
            }
        }
    }

    function collidersOf(target) {
        const array = target.add(collidersOffset).readPointer();
        if (array.isNull()) return [];
        const length = array.add(24).readU64().toNumber();
        if (length < 0 || length > 32) return [];
        const result = [];
        for (let index = 0; index < length; ++index) {
            const collider = array.add(
                32 + index * Process.pointerSize).readPointer();
            if (!collider.isNull()) result.push(collider);
        }
        return result;
    }

    function discoverRange() {
        if (rangeHandle !== 0) {
            handleFree(rangeHandle);
            rangeHandle = 0;
        }
        const found = [];
        const callback = new NativeCallback(function (array, count, _user) {
            for (let index = 0; index < count; ++index)
                found.push(array.add(index * Process.pointerSize).readPointer());
        }, 'void', ['pointer', 'int', 'pointer'], abi);
        const state = liveBegin(rangeClass, 1000, callback, ptr(0), noop, noop);
        liveStatics(state);
        liveEnd(state);
        for (const candidate of found) {
            if (unityAlive(candidate)) {
                rangeHandle = handleNew(candidate, 0);
                break;
            }
        }
        lastDiscovery = Date.now();
        send({kind: 'ready', training: rangeHandle !== 0});
    }

    function poolObjects(pool) {
        if (pool.isNull()) return [];
        if (poolItemsOffset === null)
            poolItemsOffset = fieldOffset(objectGetClass(pool), '<Items>k__BackingField');
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
            const object = array.add(32 + index * Process.pointerSize).readPointer();
            if (!object.isNull()) objects.push(object);
        }
        return objects;
    }

    function scan() {
        if (rangeHandle === 0) {
            if (Date.now() - lastDiscovery > 500) discoverRange();
            return;
        }
        const manager = handleTarget(rangeHandle);
        if (!unityAlive(manager)) {
            rangeHandle = 0;
            return;
        }
        try {
            if (!Boolean(getActiveAndEnabled(manager))) return;
        } catch (_) { return; }

        const now = Date.now();
        const targets = new Map();
        const currentTargetKeys = new Set();
        for (const poolOffset of poolOffsets) {
            const pool = manager.add(poolOffset).readPointer();
            for (const target of poolObjects(pool)) {
                let enabled = false;
                try {
                    enabled = Boolean(getActiveAndEnabled(target));
                } catch (_) { continue; }
                const key = target.toString();
                if (!enabled) continue;
                currentTargetKeys.add(key);
                if (!previousTargetKeys.has(key)) {
                    targetBirth.set(key, now);
                    forcedTargetScores.delete(key);
                }
                const classAddress = objectGetClass(target);
                const classNameAddress = classGetName(classAddress);
                const className = classNameAddress.isNull() ? '?' : classNameAddress.readUtf8String();
                const missed = Boolean(target.add(missedOffset).readU8());
                const center = centerOf(target, className);
                // Do not filter on TargetBase.starHit: Reset() intentionally does
                // not clear it, so pooled targets can retain a stale true value.
                // A successfully hit real target disables its Behaviour at once,
                // which is the reliable one-shot condition used above.
                if (!missed && center !== null) {
                    targets.set(key, {
                        pointer: target,
                        key: key,
                        className: className,
                        center: center,
                        depth: depthLevel(target, targetDepthOffset),
                        born: targetBirth.get(key) || now
                    });
                }
            }
        }
        previousTargetKeys = currentTargetKeys;
        for (const key of Array.from(targetBirth.keys())) {
            if (!currentTargetKeys.has(key)) targetBirth.delete(key);
        }
        currentTargetsForShot = Array.from(targets.values());
        for (const key of Array.from(forcedTargetScores.values())) {
            if (!currentTargetKeys.has(key)) forcedTargetScores.delete(key);
        }

        const heroBow = manager.add(heroBowOffset).readPointer();
        let bowCanShoot = false;
        let bowMustAutoShoot = false;
        let bowReadyToDraw = false;
        const activeArrows = [];
        if (unityAlive(heroBow)) {
            bowCanShoot = Boolean(heroBow.add(bowCanShootOffset).readU8());
            bowMustAutoShoot = Boolean(
                heroBow.add(bowMustAutoShootOffset).readU8());
            const animation = heroBow.add(bowAnimationOffset).readPointer();
            if (!animation.isNull()) {
                const loading = Boolean(isAnimationPlaying(
                    animation, loadAnimationName, -1.0));
                const holding = Boolean(isAnimationPlaying(
                    animation, holdAnimationName, -1.0));
                const shooting = Boolean(isAnimationPlaying(
                    animation, shootAnimationName, -1.0));
                bowReadyToDraw = !loading && !holding && !shooting;
                // The original game waits for 80% of the load animation. Once the
                // game has actually entered that animation, arm the shot at once.
                // This preserves the real press/load/release path while removing
                // the long fixed draw time and adapting to any game speed.
                if (active && guideArrows && loading) {
                    heroBow.add(bowCanShootOffset).writeU8(1);
                    heroBow.add(bowMustAutoShootOffset).writeU8(1);
                    bowCanShoot = true;
                    bowMustAutoShoot = true;
                }
            }
            const arrowPool = heroBow.add(arrowPoolOffset).readPointer();
            for (const arrow of poolObjects(arrowPool)) {
                let enabled = false;
                try {
                    enabled = Boolean(getActiveAndEnabled(arrow));
                } catch (_) { continue; }
                if (enabled && !Boolean(arrow.add(arrowCollidedOffset).readU8()))
                    activeArrows.push(arrow);
            }
        }

        const reserved = new Set();
        for (const arrow of activeArrows) {
            const key = arrow.toString();
            const assignment = arrowAssignments.get(key);
            if (assignment !== undefined) reserved.add(assignment.targetKey);
        }

        function nearestUnreserved(minimumDepth) {
            if (minimumDepth === undefined) minimumDepth = Number.NEGATIVE_INFINITY;
            let best = null;
            for (const target of targets.values()) {
                if (reserved.has(target.key)) continue;
                // Arrow.Reset starts at Depth.Min and Arrow.Update only increases
                // its level. Targets are therefore encountered from smaller to
                // larger depth values.
                if (target.depth + 0.25 < minimumDepth) continue;
                if (best === null || target.depth < best.depth ||
                        (target.depth === best.depth && target.born < best.born))
                    best = target;
            }
            return best;
        }

        const currentArrowKeys = new Set();
        for (const arrow of activeArrows) {
            const arrowKey = arrow.toString();
            currentArrowKeys.add(arrowKey);
            const newArrow = !previousArrowKeys.has(arrowKey);
            if (newArrow) shotSequence += 1;
            // Arrow.Reset may already have assigned this arrow synchronously. Do
            // not overwrite that immediate assignment on the first polling pass.
            if (!arrowAssignments.has(arrowKey)) {
                const target = nearestUnreserved();
                if (target !== null) {
                    arrowAssignments.set(arrowKey, {
                        targetKey: target.key,
                        lastCenter: target.center
                    });
                    reserved.add(target.key);
                }
            }
            let assignment = arrowAssignments.get(arrowKey);
            if (assignment !== undefined) {
                const target = targets.get(assignment.targetKey);
                if (target !== undefined) {
                    assignment.lastCenter = target.center;
                } else {
                    // The previous target was hit. Because arrows pierce targets,
                    // bend the same live arrow toward the nearest remaining target
                    // that is ahead of the arrow's current depth.
                    reserved.delete(assignment.targetKey);
                    const arrowDepth = depthLevel(arrow, arrowDepthOffset);
                    const next = nearestUnreserved(arrowDepth);
                    if (next !== null) {
                        assignment = {
                            targetKey: next.key,
                            lastCenter: next.center
                        };
                        arrowAssignments.set(arrowKey, assignment);
                        reserved.add(next.key);
                    }
                }
                const point = assignment.lastCenter;
                if (active && guideArrows && point !== null)
                    writeArrowAim(arrow, point);
            }
        }
        previousArrowKeys = currentArrowKeys;
        for (const key of Array.from(arrowAssignments.keys())) {
            if (!currentArrowKeys.has(key)) arrowAssignments.delete(key);
        }

        // TargetDummy's CircleCollider2D is its exact star zone, so only its
        // non-star body colliders are suppressed. Every target, including coins,
        // stays tangible and also participates in guaranteed memory scoring.
        const desiredDisabled = new Map();
        function requestDisabled(owner, collider) {
            desiredDisabled.set(collider.toString(), {owner: owner, collider: collider});
        }
        if (active && guideArrows) {
            for (const target of targets.values()) {
                if (target.className !== 'TargetDummy') continue;
                for (const collider of collidersOf(target.pointer)) {
                    const colliderClass = colliderClassName(collider);
                    if (colliderClass !== 'CircleCollider2D')
                        requestDisabled(target.pointer, collider);
                }
            }
        }
        for (const [key, entry] of Array.from(disabledColliders.entries())) {
            if (desiredDisabled.has(key)) continue;
            let ownerEnabled = false;
            try { ownerEnabled = Boolean(getEnabled(entry.owner)); } catch (_) {}
            if (ownerEnabled) {
                try { setEnabled(entry.collider, 1); } catch (_) {}
            }
            disabledColliders.delete(key);
        }
        for (const [key, entry] of desiredDisabled.entries()) {
            if (disabledColliders.has(key)) continue;
            try {
                if (Boolean(getEnabled(entry.collider))) {
                    setEnabled(entry.collider, 0);
                    disabledColliders.set(key, entry);
                }
            } catch (_) {}
        }

        const nextTarget = nearestUnreserved();
        pendingTarget = nextTarget;
        if (!active || nextTarget === null) return;
        const camera = getMainCamera();
        if (camera.isNull()) return;
        const world = Memory.alloc(16);
        world.writeFloat(nextTarget.center.x);
        world.add(4).writeFloat(nextTarget.center.y);
        world.add(8).writeFloat(nextTarget.center.z);
        const screen = Memory.alloc(16);
        worldToScreen(camera, world, 0, screen);
        const screenX = screen.readFloat();
        const screenY = screen.add(4).readFloat();
        if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return;
        if (now - lastSend >= 4) {
            lastSend = now;
            send({
                kind: 'aim',
                pointer: nextTarget.key,
                className: nextTarget.className,
                generation: nextTarget.born,
                bowCanShoot: bowCanShoot,
                bowMustAutoShoot: bowMustAutoShoot,
                bowReadyToDraw: bowReadyToDraw,
                activeArrowCount: activeArrows.length,
                shotSequence: shotSequence,
                screenX: screenX,
                screenY: screenY,
                worldX: nextTarget.center.x,
                worldY: nextTarget.center.y
            });
        }
    }

    // A close target can be reached before the 2 ms polling loop sees a newly
    // spawned arrow. Aim it inside Arrow.Reset itself, before its first Update.
    Interceptor.attach(compileMethod(findMethod(arrowClass, 'Reset', 0, false)), {
        onEnter(args) {
            this.arrow = hookArgument(this, args, 0);
        },
        onLeave(_retval) {
            if (!active || !guideArrows || pendingTarget === null) return;
            try {
                const center = centerOf(
                    pendingTarget.pointer, pendingTarget.className);
                if (center === null) return;
                const arrowKey = this.arrow.toString();
                arrowAssignments.set(arrowKey, {
                    targetKey: pendingTarget.key,
                    lastCenter: center
                });
                writeArrowAim(this.arrow, center);
                scoreGuaranteedShotTargets();
            } catch (_) {}
        }
    });

    discoverRange();
    return scan;
}

runOnMonoThread(start, 2);

rpc.exports = {
    setActive(value, allowWrite) {
        active = Boolean(value);
        guideArrows = Boolean(allowWrite);
        return active;
    }
};
"""


class ArcheryMemory:
    def __init__(
        self,
        pid: int,
        on_aim: Callable[[dict], None],
        on_status: Callable[[str], None] = print,
    ) -> None:
        self._on_aim = on_aim
        self._on_status = on_status
        self._errors = AgentErrors(on_status)
        self._lock = threading.Lock()
        self._closed = False
        # Under Wine this also makes the process safe for Frida to attach.
        source = agent_source(pid, _AGENT_SOURCE)
        self._session = frida.attach(pid)
        self._script = self._session.create_script(source, name="archery")
        self._script.on("message", self._handle_message)
        self._script.load()

    def _handle_message(self, message: dict, _data: bytes | None) -> None:
        if message.get("type") != "send":
            self._errors.report(message)
            return
        payload = message.get("payload", {})
        if payload.get("kind") == "aim":
            self._on_aim(payload)
        elif payload.get("kind") == "ready":
            value = "yes" if payload.get("training") else "no"
            self._on_status(f"Archery memory ready: training={value}.")
        elif payload.get("kind") == "runtimeError":
            self._on_status(f"Memory runtime error: {payload.get('message')}")

    def set_active(self, active: bool, guide_arrows: bool = True) -> None:
        with self._lock:
            if not self._closed:
                self._script.exports_sync.set_active(
                    bool(active), bool(guide_arrows)
                )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._script.unload()
            finally:
                self._session.detach()

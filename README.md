# Action Jackson — Blender Retargeting + Export Toolkit

*Onigiri-style bone retargeting, fast renaming, live pose mirroring, and game-ready exports (FBX/BVH) — all in one panel.*



---

## ✨ Features

* **Onigiri-style retarget workflow**

  * Choose **Source** and **Target** armatures.
  * **Capture Pair (Source→Rename)**: click a source bone, click a target bone, capture → auto-maps “Original → Rename”.
  * **3-column list:** **1-Original**, **2-Current**, **3-Rename**.
  * **Revert** per row (Current → Original).
  * **Auto-populate** list when Source/Target is first set.

* **Smart renaming (Original → Rename)**

  * Applies in **Edit Mode** with a safe two-phase rename to avoid name collisions.
  * Preserves bone flags (e.g., **use\_deform**).
  * **Patches everything**: mesh vertex groups, constraint subtargets, F-Curves, Drivers.
  * Optional: **Temporarily Apply Rename On Export** (auto-revert after export).

* **Selection & visibility helpers**

  * **Retarget Source and Target**: colors Source bones **red**, Target bones **blue**.
  * **Mapped = green**: captured or applied rows are grouped “Mapped (Green)” so you can see what’s done.
  * **Refresh Mapped Colors** button if you change mappings later.
  * **Pose Mode / Object Mode** one-click buttons.
  * **X-Ray toggles** per rig.
  * **Auto-Sync**: when you select a bone in either rig, the list follows the row; vice-versa (optional).

* **Save / Load mapping (merge-safe)**

  * Text format: `Original=Rename` (with `#` comments).
  * **Load** merges into existing rows (doesn’t wipe your list).
  * **Save** writes the visible mapping (Original→Rename).

* **Live Follow (preview retarget)**

  * Drive Target from Source or Source from Target.
  * **Rotation Only** or **Full Transforms** (via lightweight constraints).
  * One-click **Remove** clears all follow constraints.
  * Great for visual verification before you commit.

* **Exports built for DCC/engines (FBX & BVH)**

  * **Global Scale** (customizable).
  * **Custom Rotation offsets (X/Y/Z degrees)**:

    * **FBX:** applied via object world rotation at export (auto-revert).
    * **BVH:** applied by a temporary world-space rotation on root bones (baked, auto-clean).
  * **Export Current Action (Single File)** — just what’s active in the Action Editor.
  * **Export All Actions (Per File)** — batch one file per Action (FBX or BVH).
  * **Export FBX (All Actions in One File)** — multi-take FBX (one file, many actions).
  * FBX options: **Include Meshes**, **Add Leaf Bones**, **Bake Simplify**.
  * **Defaults that stick**: Save/Load defaults (Scale/Rotation) to add-on prefs.

---

## 🧩 Install

1. **Download** the latest `action_jackson_*.py`.
2. Blender → **Edit ▸ Preferences ▸ Add-ons ▸ Install…**
3. Pick the `.py`, enable **Action Jackson**.
4. Open the **N-panel** → **Action Jackson** tab.

> Requires **Blender 3.0.0**.

---

## 🚀 Quick Start

1. **Pick your rigs**: set **Source** and **Target** (armatures only).
2. Click **Retarget Source and Target** (red/blue) to visualize.
3. **Capture Pair (Source→Rename)**:

   * Select a bone on **Source** (A), select the matching bone on **Target** (B), then click the button.
   * Green “Mapped” color shows for mapped bones.
4. (Optional) **Live Follow** → toggle **Source→Target** (Rotation Only) and pose to verify.
5. **Apply to Target (Original→Rename)** (or to Source) to commit the names.
6. **Save** your map as `.txt` for reuse.
7. **Export**:

   * Set **Format** (FBX/BVH), **Scale**, and **Rot X/Y/Z°**.
   * Use **Export Current Action (Single File)** for one anim,
     or **Export All Actions (Per File)** for a batch,
     or **FBX (All Actions in One File)** for multi-take.

---

## 🗂 Mapping TXT Format

```text
# Lines starting with # are comments
# OriginalName=RenameName
mPelvis=hips
mShoulderLeft=leftUpperArm
mElbowLeft=leftLowerArm
mWristLeft=leftHand
```

* **Load…** merges into your current list (won’t delete other rows).
* **Save** writes the visible Original→Rename pairs.

---

## 🎮 Notes for Unreal

* **Tiny animations** → set **Scale** to `100` (meters→centimeters) or `1000` if your source is small.
* **Upside-down**:

  * Try **Rot X° = -90** or **90** at export.
  * It’s normal for **Skeleton view** to look face-down while **Animations** look correct — the animation has a baked up-axis fix, but the Skeleton’s retarget base pose is still the original.

    * Optionally set **Skeleton → Retarget Base Pose** in UE to an upright reference.

---

## 🛠 UI Tour

* **Source / Target**: eyedroppers accept **Armature** objects only.
* **Current Column**: choose whether it reflects Source or Target (helps inspection).
* **Row actions**:

  * **Revert** (↩): Current → Original; also renames in the rig when applicable.
  * **Select All** checkbox for batch operations (e.g., batch revert).
* **Live Follow**:

  * Mode: **Rotation Only** (safer) or **Full Transforms** (1:1 when rest poses match).
  * Direction: **Source→Target** or **Target→Source** (mutually exclusive).
  * **Remove** to clear all constraints it added.

---

## 🧪 Troubleshooting

* **BVH rotation didn’t change**
  Ensure you’re using the panel’s **Rot X/Y/Z°** (BVH uses a temp world-space rotation baked into roots). Try X = ±90 first.

* **Auto-Sync not selecting rows**
  Ensure **Auto-Sync** is enabled. It matches on any of the three names (Original/Current/Rename).

* **Renames didn’t propagate**
  The add-on updates vertex groups, constraint subtargets, F-Curves and Drivers. If something custom still references old names, reload your scene dependencies and try **Apply** again.

* **“Not enough arm bones” in another tool**
  Make sure your mapping uses the names that tool expects (e.g., VRM humanoid). Load a map and **Apply** before export.

---

## 📦 Changelog (highlights)

* 3-column mapping (**Original / Current / Rename**), merge-safe **Load**, **Save**.
* **Auto-populate** list on selecting Source/Target.
* Bone coloring (**Source=red**, **Target=blue**, **Mapped=green**).
* **Live Follow** (Rotation Only / Full Transforms; A→B or B→A).
* **Export Current Action**, **Per-Action batch**, **FBX multi-take**.
* **Custom Scale** + **Custom Rotation (X/Y/Z°)** for both FBX/BVH.
* **BVH rotation bake** via temporary root constraints.
* Preferences: **Save Defaults / Load Defaults** (Scale/Rotation).

---

## 🤝 Contributing

Issues and PRs welcome!
Please include:

* Blender version, OS, and steps to reproduce.
* A minimal `.blend` or a small BVH/FBX snippet if possible.

---

## 📜 License

**MIT** — see the SPDX header in the source.

---

## 💬 Credits

Built with a lot of animator feedback. Thanks for pushing it to be fast and practical!

# SPDX-License-Identifier: MIT
bl_info = {
    "name": "Action Jackson",
    "author": "The Mighty Ginkgo+Gpt5",
    "version": (1, 8, 4),
    "blender": (3, 0, 0),
    "location": "3D View > N-Panel > Action Jackson",
    "description": "Onigiri-style retarget tool with 3-column map, save/load, autosync, exporters, custom scale/rotation, and live follow pairing. Mapped bones turn green.",
    "category": "Animation",
}

import bpy
import os
import re
from math import radians, floor, ceil
from mathutils import Matrix, Euler
from bpy.props import (
    StringProperty, BoolProperty, FloatProperty, PointerProperty,
    EnumProperty, CollectionProperty, IntProperty
)
from bpy.types import UIList, Operator, AddonPreferences

# ----------------------
# Add-on Preferences (save defaults)
# ----------------------

class AJ_Prefs(AddonPreferences):
    bl_idname = __name__

    default_scale: FloatProperty(name="Default Export Scale", default=100.0, min=0.001)
    default_rot_x: FloatProperty(name="Default Rot X°", default=0.0)
    default_rot_y: FloatProperty(name="Default Rot Y°", default=0.0)
    default_rot_z: FloatProperty(name="Default Rot Z°", default=0.0)

    def draw(self, context):
        lay = self.layout
        box = lay.box()
        box.label(text="Action Jackson Defaults")
        col = box.column(align=True)
        col.prop(self, "default_scale")
        col.prop(self, "default_rot_x")
        col.prop(self, "default_rot_y")
        col.prop(self, "default_rot_z")
        box.label(text="Use 'Load Defaults' in the panel to apply to this file.")

# ----------------------
# Helpers
# ----------------------

def parse_mapping_txt(text):
    mapping = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            old, new = line.split("=", 1)
            old = old.strip()
            new = new.strip()
            if old:
                mapping[old] = new
    return mapping

def mapping_to_txt(mapping):
    lines = ["# bone_old=bone_new", "# lines starting with # are comments"]
    for k, v in mapping.items():
        lines.append(f"{k}={v}")
    return "\n".join(lines)

def _ensure_active(obj):
    if bpy.context.view_layer.objects.active is not obj:
        bpy.context.view_layer.objects.active = obj

def _ensure_mode(obj, mode):
    if not obj: return
    if obj.mode != mode:
        try:
            bpy.ops.object.mode_set(mode=mode)
        except Exception:
            pass

def _bone_exists(arm_obj, name):
    try:
        return bool(name) and (name in arm_obj.data.bones)
    except Exception:
        return False

def get_active_bone_name(arm_obj):
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return None
    bdat = getattr(arm_obj.data.bones, "active", None)
    if bdat:
        return bdat.name
    if getattr(arm_obj, "pose", None):
        for pb in arm_obj.pose.bones:
            if pb.bone.select:
                return pb.name
    return None

def _select_pose_bone(arm, name):
    if not arm or arm.type != 'ARMATURE' or not name or not _bone_exists(arm, name):
        return False
    _ensure_active(arm); _ensure_mode(arm, 'POSE')
    try:
        for pb in arm.pose.bones:
            pb.bone.select = False
        pb = arm.pose.bones.get(name)
        if pb:
            pb.bone.select = True
            arm.data.bones.active = arm.data.bones.get(name, None)
            return True
    except Exception:
        pass
    return False

def redraw_all_view3d(context):
    scr = context.window.screen if context.window else None
    if not scr: return
    for area in scr.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()

def _replace_bone_in_datapath(dp, old, new):
    token = f'pose.bones["{old}"]'
    if dp and token in dp:
        return dp.replace(token, f'pose.bones["{new}"]')
    return None

def _populate_bone_map_from(props, arm):
    props.bone_map.clear()
    if not arm or arm.type != 'ARMATURE':
        return 0
    for b in arm.data.bones:
        it = props.bone_map.add()
        it.original_name = b.name
        it.current_name = b.name
        it.target_name = b.name
        it.cap_src = b.name
        it.cap_tgt = b.name
    props.active_index = 0 if len(props.bone_map) else -1
    return len(props.bone_map)

# Colors / groups
def _get_or_make_group(arm, name, color_rgb):
    if not arm or arm.type != 'ARMATURE' or not getattr(arm, "pose", None):
        return None
    grp = arm.pose.bone_groups.get(name)
    if grp is None:
        grp = arm.pose.bone_groups.new(name=name)
    try:
        grp.color_set = 'CUSTOM'
        r,g,b = color_rgb
        grp.colors.normal = (r, g, b)
        grp.colors.select = (min(1.0,r+0.2), min(1.0,g+0.2), min(1.0,b+0.2))
        grp.colors.active = (1.0, 1.0, 1.0)
    except Exception:
        pass
    return grp

def _assign_group_to(arm, bone_name, grp):
    if not arm or not grp or not getattr(arm, "pose", None): return
    pb = arm.pose.bones.get(bone_name)
    if pb:
        pb.bone_group = grp

def _mark_mapped_green(props):
    A = props.source_armature; B = props.target_armature
    gA = _get_or_make_group(A, "Mapped (Green)", (0.2, 1.0, 0.2)) if A else None
    gB = _get_or_make_group(B, "Mapped (Green)", (0.2, 1.0, 0.2)) if B else None
    countA = countB = 0
    for it in props.bone_map:
        if it.original_name and it.target_name and it.target_name != "":
            if A and _bone_exists(A, it.original_name) and gA:
                _assign_group_to(A, it.original_name, gA); countA += 1
            if B and _bone_exists(B, it.target_name) and gB:
                _assign_group_to(B, it.target_name, gB); countB += 1
    return countA, countB

# ----------------------
# Rename / patch routines
# ----------------------

def rename_bones_edit_mode_preserve_flags(arm_obj, mapping):
    if not arm_obj or arm_obj.type != 'ARMATURE' or not mapping:
        return 0, 0
    deform_flags = {b.name: b.use_deform for b in arm_obj.data.bones}
    temp_suffix = "_TMP_RENAME__"
    _ensure_active(arm_obj); _ensure_mode(arm_obj, 'EDIT')
    eb = arm_obj.data.edit_bones

    pairs = [(o, n) for o, n in mapping.items() if o in eb.keys() and n and o != n]

    for old, new in pairs:
        eb[old].name = new + temp_suffix

    applied = 0
    for old, new in pairs:
        tmp = new + temp_suffix
        if tmp in eb.keys():
            eb[tmp].name = new
            applied += 1

    _ensure_mode(arm_obj, 'OBJECT')

    restored = 0
    for old, new in pairs:
        if new in arm_obj.data.bones and old in deform_flags:
            arm_obj.data.bones[new].use_deform = deform_flags[old]
            restored += 1
    return applied, restored

def iter_meshes_using_armature(arm_obj):
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and getattr(mod, "object", None) == arm_obj:
                yield obj, mod
                break

def patch_vertex_groups_for_armature(arm_obj, mapping):
    count = 0
    for obj, mod in iter_meshes_using_armature(arm_obj):
        for vg in obj.vertex_groups:
            new = mapping.get(vg.name)
            if new and new != vg.name:
                vg.name = new
                count += 1
    return count

def patch_constraints_subtargets(arm_obj, mapping):
    patched = 0
    for pbone in arm_obj.pose.bones:
        for con in pbone.constraints:
            if hasattr(con, "subtarget") and con.subtarget in mapping:
                con.subtarget = mapping[con.subtarget]
                patched += 1
    return patched

def patch_fcurves_actions(mapping):
    changed = 0
    for act in bpy.data.actions:
        for fc in act.fcurves:
            dp = fc.data_path
            if not dp:
                continue
            new_dp = dp
            replaced_any = False
            for old, new in mapping.items():
                res = _replace_bone_in_datapath(new_dp, old, new)
                if res and res != new_dp:
                    new_dp = res
                    replaced_any = True
            if replaced_any:
                fc.data_path = new_dp
                changed += 1
    return changed

def patch_driver_fcurves(mapping):
    changed = 0
    def handle_animdata(ad):
        nonlocal changed
        if not ad: return
        if ad.drivers:
            for d in ad.drivers:
                dp = d.data_path or ""
                new_dp = dp
                replaced_any = False
                for old, new in mapping.items():
                    res = _replace_bone_in_datapath(new_dp, old, new)
                    if res and res != new_dp:
                        new_dp = res
                        replaced_any = True
                if replaced_any:
                    d.data_path = new_dp
                    changed += 1
        if ad.action:
            for fc in ad.action.fcurves:
                dp = fc.data_path or ""
                new_dp = dp
                replaced_any = False
                for old, new in mapping.items():
                    res = _replace_bone_in_datapath(new_dp, old, new)
                    if res and res != new_dp:
                        new_dp = res
                        replaced_any = True
                if replaced_any:
                    fc.data_path = new_dp
                    changed += 1
    for obj in bpy.data.objects:
        handle_animdata(getattr(obj, "animation_data", None))
    for arm in bpy.data.armatures:
        handle_animdata(getattr(arm, "animation_data", None))
    for sk in bpy.data.shape_keys:
        handle_animdata(getattr(sk, "animation_data", None))
    for mat in bpy.data.materials:
        handle_animdata(getattr(mat, "animation_data", None))
    for wc in bpy.data.worlds:
        handle_animdata(getattr(wc, "animation_data", None))
    return changed

def force_refresh_depsgraph(arm_obj):
    for obj, mod in iter_meshes_using_armature(arm_obj):
        orig = mod.object
        mod.object = None
        bpy.context.view_layer.update()
        mod.object = orig
        bpy.context.view_layer.update()
    scene = bpy.context.scene
    cur = scene.frame_current
    scene.frame_set(cur + 1)
    scene.frame_set(cur)

# ----------------------
# Properties
# ----------------------

class BoneMapItem(bpy.types.PropertyGroup):
    original_name: StringProperty(name="Original")
    current_name: StringProperty(name="Current")
    target_name: StringProperty(name="Rename")
    cap_src: StringProperty(name="Captured Source", default="")
    cap_tgt: StringProperty(name="Captured Target", default="")
    mark: BoolProperty(name="Mark", default=False)

def armature_object_poll(self, obj):
    return getattr(obj, "type", None) == 'ARMATURE'

def _on_mark_all_changed(self, context):
    props = context.scene.aj_props
    val = props.mark_all
    for it in props.bone_map:
        it.mark = val

def _on_src_tgt_changed(self, context):
    try:
        props = context.scene.aj_props
    except Exception:
        return
    if len(props.bone_map) > 0:
        return
    arm = props.source_armature or props.target_armature
    if arm and arm.type == 'ARMATURE':
        _populate_bone_map_from(props, arm)

def _select_for_index(context, idx):
    props = context.scene.aj_props
    if not props.follow_list_selection:
        return
    A = props.source_armature
    B = props.target_armature
    if 0 <= idx < len(props.bone_map):
        it = props.bone_map[idx]
        if A:
            for name in (it.original_name, it.current_name, it.target_name):
                if _select_pose_bone(A, name):
                    break
        if B:
            for name in (it.target_name, it.current_name, it.original_name):
                if _select_pose_bone(B, name):
                    break

def _on_active_index_changed(self, context):
    _select_for_index(context, context.scene.aj_props.active_index)

class AJ_Props(bpy.types.PropertyGroup):
    # Export props
    output_dir: StringProperty(name="Output Folder", subtype='DIR_PATH', default="")
    export_format: EnumProperty(name="Format", items=[('FBX',"FBX",""),('BVH',"BVH","")], default='FBX')
    include_mesh: BoolProperty(name="Include Meshes (FBX only)", default=True)
    add_leaf_bones: BoolProperty(name="Add Leaf Bones (FBX only)", default=False)
    simplify: FloatProperty(name="Bake Simplify", min=0.0, soft_max=1.0, default=0.0)
    export_side: EnumProperty(name="Armature", items=[('SOURCE',"Source",""), ('TARGET',"Target","")], default='TARGET')
    rename_on_export: BoolProperty(
        name="Temporarily Apply Rename On Export",
        description="Rename Original→Rename on the chosen armature for export, then revert",
        default=True
    )
    export_scale: FloatProperty(name="Scale", default=100.0, min=0.001)
    rot_x: FloatProperty(name="Rot X°", default=0.0)
    rot_y: FloatProperty(name="Rot Y°", default=0.0)
    rot_z: FloatProperty(name="Rot Z°", default=0.0)

    # Mapping store
    bone_map: CollectionProperty(type=BoneMapItem)
    mapping_path: StringProperty(name="Mapping Path", subtype='FILE_PATH', default="")
    active_index: IntProperty(name="Active", default=0, update=_on_active_index_changed)
    auto_sync: BoolProperty(name="Auto-Sync Selection", default=False)

    # Source/Target
    source_armature: PointerProperty(name="Source", type=bpy.types.Object, poll=armature_object_poll, update=_on_src_tgt_changed)
    target_armature: PointerProperty(name="Target", type=bpy.types.Object, poll=armature_object_poll, update=_on_src_tgt_changed)

    # Column behavior
    current_armature_side: EnumProperty(
        name="Current Column Uses",
        description="Which armature the Current column should reflect",
        items=[('TARGET', "Target", ""), ('SOURCE', "Source", "")],
        default='TARGET'
    )

    # Selection assist
    follow_list_selection: BoolProperty(
        name="Follow Row Selection",
        description="Selecting a row will also try to select the corresponding bones",
        default=True
    )

    # Header select-all for marks
    mark_all: BoolProperty(name="Select All", default=False, update=_on_mark_all_changed)

    # Live follow
    follow_mode: EnumProperty(
        name="Live Follow Mode",
        items=[('ROT', "Rotation Only", ""), ('XFORM', "Full Transforms", "")],
        default='ROT'
    )
    live_follow_a2b: BoolProperty(name="Source → Target", default=False)
    live_follow_b2a: BoolProperty(name="Target → Source", default=False)

# ----------------------
# UIList
# ----------------------

class AJ_UL_bone_map(UIList):
    bl_idname = "AJ_UL_bone_map"
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "mark", text="")
        row.prop(item, "original_name", text="", emboss=True)

        op = row.operator("aj.revert_row", text="", icon='LOOP_BACK', emboss=False)
        op.index = index

        row.prop(item, "current_name", text="", emboss=True)
        row.label(text="→")
        row.prop(item, "target_name", text="", emboss=True)

# ----------------------
# Operators - Renamer / Mapped coloring
# ----------------------

class AJ_OT_mark_mapped_colors(Operator):
    bl_idname = "aj.mark_mapped_colors"
    bl_label = "Refresh Mapped Colors"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.aj_props
        ca, cb = _mark_mapped_green(props)
        self.report({'INFO'}, f"Mapped coloring applied. Source: {ca}, Target: {cb}")
        return {'FINISHED'}

class AJ_OT_revert_row(Operator):
    bl_idname = "aj.revert_row"
    bl_label = "Revert this row (Current→Original)"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)

    def execute(self, context):
        p = context.scene.aj_props
        idx = self.index
        if idx < 0 or idx >= len(p.bone_map):
            self.report({'ERROR'}, "Invalid row index."); return {'CANCELLED'}
        it = p.bone_map[idx]
        arm = p.target_armature if p.current_armature_side == 'TARGET' else p.source_armature

        cur = it.current_name or ""
        ori = it.original_name or ""
        it.current_name = ori

        if arm and arm.type == 'ARMATURE' and cur and ori and cur != ori and _bone_exists(arm, cur):
            mapping = {cur: ori}
            rename_bones_edit_mode_preserve_flags(arm, mapping)
            patch_vertex_groups_for_armature(arm, mapping)
            patch_constraints_subtargets(arm, mapping)
            patch_fcurves_actions(mapping)
            patch_driver_fcurves(mapping)
            force_refresh_depsgraph(arm)
            self.report({'INFO'}, f"Reverted '{cur}' → '{ori}'.")
            return {'FINISHED'}

        self.report({'INFO'}, "Reverted UI Current → Original.")
        return {'FINISHED'}

class AJ_OT_capture_pair(Operator):
    bl_idname = "aj.capture_pair"
    bl_label = "Capture Pair (Source→Rename)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        A = p.source_armature
        B = p.target_armature
        if not (A and B and A.type == 'ARMATURE' and B.type == 'ARMATURE'):
            self.report({'ERROR'}, "Pick valid Source and Target armatures.")
            return {'CANCELLED'}

        _ensure_active(A); _ensure_mode(A, 'POSE')
        _ensure_active(B); _ensure_mode(B, 'POSE')

        a_name = get_active_bone_name(A)
        b_name = get_active_bone_name(B)
        if not a_name or not b_name:
            self.report({'ERROR'}, "Select an active bone on Source and on Target, then try again.")
            return {'CANCELLED'}

        existing_index = None
        for i, it in enumerate(p.bone_map):
            if it.original_name == a_name:
                it.target_name = b_name
                it.cap_src = a_name
                it.cap_tgt = b_name
                it.current_name = (b_name if p.current_armature_side == 'TARGET' else a_name)
                existing_index = i
                break
        if existing_index is None:
            it = p.bone_map.add()
            it.original_name = a_name
            it.current_name = (b_name if p.current_armature_side == 'TARGET' else a_name)
            it.target_name = b_name
            it.cap_src = a_name
            it.cap_tgt = b_name
            p.active_index = len(p.bone_map) - 1
        else:
            p.active_index = existing_index

        # mark green
        _mark_mapped_green(p)

        self.report({'INFO'}, f"Captured: Original='{a_name}' → Rename='{b_name}'")
        return {'FINISHED'}

class AJ_OT_refresh_current(Operator):
    bl_idname = "aj.refresh_current"
    bl_label = "Refresh Current from Rig"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        p = context.scene.aj_props
        arm = p.target_armature if p.current_armature_side == 'TARGET' else p.source_armature
        if not (arm and arm.type == 'ARMATURE'):
            self.report({'INFO'}, "No rig set — Current stays as-is.")
            return {'CANCELLED'}
        count = 0
        for it in p.bone_map:
            if _bone_exists(arm, it.original_name):
                it.current_name = it.original_name; count += 1
            elif _bone_exists(arm, it.target_name):
                it.current_name = it.target_name; count += 1
        self.report({'INFO'}, f"Updated Current for {count} rows.")
        return {'FINISHED'}

class AJ_OT_revert_marked(Operator):
    bl_idname = "aj.revert_marked"
    bl_label = "Revert Current → Original (Marked)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        arm = p.target_armature if p.current_armature_side == 'TARGET' else p.source_armature

        mapping = {}
        for it in p.bone_map:
            if not it.mark: continue
            cur = (it.current_name or "")
            ori = (it.original_name or "")
            if arm and arm.type == 'ARMATURE' and cur and ori and cur != ori and _bone_exists(arm, cur):
                mapping[cur] = ori

        if arm and mapping:
            rename_bones_edit_mode_preserve_flags(arm, mapping)
            patch_vertex_groups_for_armature(arm, mapping)
            patch_constraints_subtargets(arm, mapping)
            patch_fcurves_actions(mapping)
            patch_driver_fcurves(mapping)
            force_refresh_depsgraph(arm)

        for it in p.bone_map:
            if it.mark and it.current_name != it.original_name:
                it.current_name = it.original_name

        self.report({'INFO'}, "Revert complete.")
        return {'FINISHED'}

# Save / Load (Original -> Rename). LOAD MERGES.

def _parse_mapping_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return parse_mapping_txt(f.read())

class AJ_OT_bone_map_save_dialog(Operator):
    bl_idname = "aj.bone_map_save"
    bl_label = "Save"
    bl_description = "Save mapping (visible Original→Rename pairs) to a .txt"
    filename_ext = ".txt"
    filter_glob: StringProperty(default="*.txt", options={'HIDDEN'})
    filepath: StringProperty(name="File Path", subtype='FILE_PATH')

    def invoke(self, context, event):
        props = context.scene.aj_props
        self.filepath = bpy.path.abspath(props.mapping_path) if props.mapping_path else bpy.path.abspath("//bone_mapping.txt")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = context.scene.aj_props
        path = bpy.path.ensure_ext(self.filepath, ".txt")
        mapping = {}
        for it in props.bone_map:
            src = it.original_name
            tgt = it.target_name
            if src and tgt:
                mapping[src] = tgt
                it.cap_src = src
                it.cap_tgt = tgt
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(mapping_to_txt(mapping))
        except Exception as e:
            self.report({'ERROR'}, f"Failed to write mapping: {e}")
            return {'CANCELLED'}
        props.mapping_path = path
        self.report({'INFO'}, f"Saved {len(mapping)} pairs to: {path}")
        return {'FINISHED'}

class AJ_OT_bone_map_load_dialog(Operator):
    bl_idname = "aj.bone_map_load"
    bl_label = "Load..."
    bl_description = "Load mapping from a .txt; MERGES into the list without deleting rows. Sets Current from chosen rig when possible."
    filter_glob: StringProperty(default="*.txt", options={'HIDDEN'})
    filepath: StringProperty(name="File Path", subtype='FILE_PATH')

    def _prefill_current_from_arm(self, props, only_if_blank=True):
        arm = props.target_armature if props.current_armature_side == 'TARGET' else props.source_armature
        if not (arm and arm.type == 'ARMATURE'):
            return 0
        count = 0
        for it in props.bone_map:
            if only_if_blank and (it.current_name or ""):
                continue
            if _bone_exists(arm, it.original_name):
                it.current_name = it.original_name; count += 1
            elif _bone_exists(arm, it.target_name):
                it.current_name = it.target_name; count += 1
        return count

    def invoke(self, context, event):
        props = context.scene.aj_props
        default = bpy.path.abspath(props.mapping_path) if props.mapping_path else bpy.path.abspath("//")
        self.filepath = default
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = context.scene.aj_props
        path = self.filepath
        if not path:
            self.report({'ERROR'}, "No file chosen.")
            return {'CANCELLED'}
        try:
            mapping = _parse_mapping_file(path)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read mapping: {e}")
            return {'CANCELLED'}

        existing_index = {it.original_name: i for i, it in enumerate(props.bone_map)}
        updated = 0
        added = 0
        for old, new in mapping.items():
            if old in existing_index:
                it = props.bone_map[existing_index[old]]
                it.target_name = new or old
                it.cap_src = old
                it.cap_tgt = new or old
                updated += 1
            else:
                it = props.bone_map.add()
                it.original_name = old
                it.target_name = new or old
                it.current_name = ""
                it.cap_src = old
                it.cap_tgt = new or old
                added += 1

        props.mapping_path = bpy.path.ensure_ext(path, ".txt")
        if len(props.bone_map) and props.active_index >= len(props.bone_map):
            props.active_index = len(props.bone_map) - 1

        filled = self._prefill_current_from_arm(props, only_if_blank=True)
        _mark_mapped_green(props)
        self.report({'INFO'}, f"Merged {updated} rows, added {added}. Current filled for {filled}.")
        return {'FINISHED'}

# Apply mapping (Original -> Rename) on chosen arm

def _apply_mapping_on_arm(arm, mapping):
    if not (arm and arm.type == 'ARMATURE'):
        return 0,0,0,0,0
    renamed, _ = rename_bones_edit_mode_preserve_flags(arm, mapping)
    vg_count = patch_vertex_groups_for_armature(arm, mapping)
    con_count = patch_constraints_subtargets(arm, mapping)
    fcu_changed = patch_fcurves_actions(mapping)
    drv_changed = patch_driver_fcurves(mapping)
    force_refresh_depsgraph(arm)
    return renamed, vg_count, con_count, fcu_changed, drv_changed

class AJ_OT_apply_to_source(Operator):
    bl_idname = "aj.apply_to_a"
    bl_label = "Apply to Source (Original→Rename)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        A = p.source_armature
        if not (A and A.type == 'ARMATURE'):
            self.report({'ERROR'}, "Pick a valid Source armature.")
            return {'CANCELLED'}
        mapping_raw = {it.original_name: it.target_name for it in p.bone_map if it.original_name and it.target_name}
        mapping = {old:new for old,new in mapping_raw.items() if old!=new and _bone_exists(A, old)}
        if not mapping:
            self.report({'INFO'}, "No applicable changes on Source (check Original names exist).")
            return {'CANCELLED'}
        renamed, vg_count, con_count, fcu_changed, drv_changed = _apply_mapping_on_arm(A, mapping)
        if p.current_armature_side == 'SOURCE':
            for it in p.bone_map:
                if it.original_name in mapping:
                    it.current_name = mapping[it.original_name]
        _mark_mapped_green(p)
        self.report({'INFO'}, f"[Source] Renamed {renamed} | VG {vg_count} | Con {con_count} | FCurves {fcu_changed} | Drivers {drv_changed}")
        return {'FINISHED'}

class AJ_OT_apply_to_target(Operator):
    bl_idname = "aj.apply_to_b"
    bl_label = "Apply to Target (Original→Rename)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        B = p.target_armature
        if not (B and B.type == 'ARMATURE'):
            self.report({'ERROR'}, "Pick a valid Target armature.")
            return {'CANCELLED'}
        mapping_raw = {it.original_name: it.target_name for it in p.bone_map if it.original_name and it.target_name}
        mapping = {old:new for old,new in mapping_raw.items() if old!=new and _bone_exists(B, old)}
        if not mapping:
            self.report({'INFO'}, "No applicable changes on Target (check Original names exist).")
            return {'CANCELLED'}
        renamed, vg_count, con_count, fcu_changed, drv_changed = _apply_mapping_on_arm(B, mapping)
        if p.current_armature_side == 'TARGET':
            for it in p.bone_map:
                if it.original_name in mapping:
                    it.current_name = mapping[it.original_name]
        _mark_mapped_green(p)
        self.report({'INFO'}, f"[Target] Renamed {renamed} | VG {vg_count} | Con {con_count} | FCurves {fcu_changed} | Drivers {drv_changed}")
        return {'FINISHED'}

# ----------------------
# Live Follow Constraints
# ----------------------

AJ_CON_PREFIX = "AJ_FOLLOW"

def _add_follow_constraints(props, direction):
    A = props.source_armature; B = props.target_armature
    if not (A and B and A.type=='ARMATURE' and B.type=='ARMATURE'):
        return 0
    count = 0
    for it in props.bone_map:
        src_name = it.original_name
        tgt_name = it.target_name
        if not (src_name and tgt_name): continue
        if direction == 'A2B':
            src_arm, src_bone = A, src_name
            dst_arm, dst_bone = B, tgt_name
        else:
            src_arm, src_bone = B, tgt_name
            dst_arm, dst_bone = A, src_name
        if not (_bone_exists(src_arm, src_bone) and _bone_exists(dst_arm, dst_bone)):
            continue
        pb = dst_arm.pose.bones.get(dst_bone)
        # Avoid duplicates by removing existing with same name
        for con in list(pb.constraints):
            if con.name.startswith(AJ_CON_PREFIX):
                pb.constraints.remove(con)
        if props.follow_mode == 'XFORM':
            con = pb.constraints.new('COPY_TRANSFORMS')
            con.name = f"{AJ_CON_PREFIX}_XFORM"
            con.target = src_arm
            con.subtarget = src_bone
            con.mix_mode = 'AFTER'
            con.target_space = 'POSE'
            con.owner_space = 'POSE'
        else:
            con = pb.constraints.new('COPY_ROTATION')
            con.name = f"{AJ_CON_PREFIX}_ROT"
            con.target = src_arm
            con.subtarget = src_bone
            con.mix_mode = 'AFTER'
            con.target_space = 'POSE'
            con.owner_space = 'POSE'
            con.use_x = con.use_y = con.use_z = True
        count += 1
    return count

def _remove_follow_constraints(props):
    A = props.source_armature; B = props.target_armature
    removed = 0
    for arm in (A, B):
        if not arm or arm.type != 'ARMATURE': continue
        for pb in arm.pose.bones:
            for con in list(pb.constraints):
                if con.name.startswith(AJ_CON_PREFIX):
                    pb.constraints.remove(con); removed += 1
    return removed

class AJ_OT_toggle_follow_a2b(Operator):
    bl_idname = "aj.toggle_follow_a2b"
    bl_label = "Live Follow: Source → Target"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        p.live_follow_a2b = not p.live_follow_a2b
        if p.live_follow_a2b:
            # ensure opposite off to avoid cycles
            p.live_follow_b2a = False
            _remove_follow_constraints(p)
            added = _add_follow_constraints(p, 'A2B')
            self.report({'INFO'}, f"Live follow A→B ON. Added {added} constraints.")
        else:
            removed = _remove_follow_constraints(p)
            self.report({'INFO'}, f"Live follow A→B OFF. Removed {removed} constraints.")
        return {'FINISHED'}

class AJ_OT_toggle_follow_b2a(Operator):
    bl_idname = "aj.toggle_follow_b2a"
    bl_label = "Live Follow: Target → Source"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        p.live_follow_b2a = not p.live_follow_b2a
        if p.live_follow_b2a:
            p.live_follow_a2b = False
            _remove_follow_constraints(p)
            added = _add_follow_constraints(p, 'B2A')
            self.report({'INFO'}, f"Live follow B→A ON. Added {added} constraints.")
        else:
            removed = _remove_follow_constraints(p)
            self.report({'INFO'}, f"Live follow B→A OFF. Removed {removed} constraints.")
        return {'FINISHED'}

class AJ_OT_clear_follow(Operator):
    bl_idname = "aj.clear_follow"
    bl_label = "Remove Live Follow"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        p.live_follow_a2b = False
        p.live_follow_b2a = False
        removed = _remove_follow_constraints(p)
        self.report({'INFO'}, f"Removed {removed} follow constraints.")
        return {'FINISHED'}

# ----------------------
# Export helpers & operators
# ----------------------

def _safe_filename(name):
    name = re.sub(r'[\\/*?:"<>|]+', "_", name)
    return re.sub(r'\s+', "_", name).strip("_")

def _iter_meshes_using_armature(arm_obj):
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and getattr(mod, "object", None) == arm_obj:
                yield obj
                break

def _select_for_export(arm_obj, include_mesh=True):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    arm_obj.select_set(True)
    _ensure_active(arm_obj)
    if include_mesh:
        for m in _iter_meshes_using_armature(arm_obj):
            m.select_set(True)

def _gather_actions_for_armature(arm_obj):
    acts = []
    for act in bpy.data.actions:
        if not act.fcurves:
            continue
        has_pose = any('pose.bones[' in (fc.data_path or '') for fc in act.fcurves)
        if has_pose:
            acts.append(act)
    ad = getattr(arm_obj, "animation_data", None)
    if ad and ad.action and (ad.action not in acts):
        acts.append(ad.action)
    return acts

def _apply_temp_mapping_if_needed(props, arm):
    if not props.rename_on_export:
        return None
    mapping_raw = {it.original_name: it.target_name for it in props.bone_map if it.original_name and it.target_name}
    mapping = {o:n for o,n in mapping_raw.items() if o!=n and _bone_exists(arm, o)}
    if not mapping:
        return None
    rename_bones_edit_mode_preserve_flags(arm, mapping)
    patch_vertex_groups_for_armature(arm, mapping)
    patch_constraints_subtargets(arm, mapping)
    patch_fcurves_actions(mapping)
    patch_driver_fcurves(mapping)
    force_refresh_depsgraph(arm)
    inv = {v:k for k,v in mapping.items()}
    return inv

def _revert_temp_mapping(inv_mapping, arm):
    if not inv_mapping:
        return
    rename_bones_edit_mode_preserve_flags(arm, inv_mapping)
    patch_vertex_groups_for_armature(arm, inv_mapping)
    patch_constraints_subtargets(arm, inv_mapping)
    patch_fcurves_actions(inv_mapping)
    patch_driver_fcurves(inv_mapping)
    force_refresh_depsgraph(arm)

def _apply_bvh_rotation_to_roots(arm, rx, ry, rz):
    empty = bpy.data.objects.new("AJ_BVH_Rotate_Helper", None)
    bpy.context.collection.objects.link(empty)
    empty.rotation_euler = Euler((radians(rx), radians(ry), radians(rz)), 'XYZ')
    roots = [pb for pb in arm.pose.bones if pb.parent is None]
    for pb in roots:
        con = pb.constraints.new('COPY_ROTATION')
        con.name = "AJ_BVH_ROT"
        con.target = empty
        con.target_space = 'WORLD'
        con.owner_space = 'WORLD'
        con.mix_mode = 'AFTER'
        con.use_x = con.use_y = con.use_z = True
    return empty

def _clear_bvh_rotation_helper(empty, arm):
    if arm and arm.type == 'ARMATURE':
        for pb in arm.pose.bones:
            for con in list(pb.constraints):
                if con.name == "AJ_BVH_ROT":
                    pb.constraints.remove(con)
    if empty and empty.name in bpy.data.objects:
        bpy.data.objects.remove(empty, do_unlink=True)

def _temporarily_rotate_world(objs, rx, ry, rz):
    originals = {obj: obj.matrix_world.copy() for obj in objs}
    R = Euler((radians(rx), radians(ry), radians(rz)), 'XYZ').to_matrix().to_4x4()
    for obj in objs:
        obj.matrix_world = R @ obj.matrix_world
    return originals

def _restore_world(objs_originals):
    for obj, mat in objs_originals.items():
        obj.matrix_world = mat

class AJ_OT_export_current_action(Operator):
    bl_idname = "aj.export_current_action"
    bl_label = "Export Current Action (Single File)"
    bl_description = "Exports only the active Action (or current pose) for the chosen armature"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        arm = p.target_armature if p.export_side == 'TARGET' else p.source_armature
        if not (arm and arm.type == 'ARMATURE'):
            self.report({'ERROR'}, f"Pick a valid {p.export_side.title()} armature.")
            return {'CANCELLED'}
        out_dir = bpy.path.abspath(p.output_dir) if p.output_dir else bpy.path.abspath("//")
        os.makedirs(out_dir, exist_ok=True)
        inv_map = _apply_temp_mapping_if_needed(p, arm)
        scene = bpy.context.scene
        ad = arm.animation_data_create()
        act = ad.action
        orig_fs, orig_fe = scene.frame_start, scene.frame_end
        if act and act.frame_range:
            scene.frame_start = int(act.frame_range[0])
            scene.frame_end   = int(act.frame_range[1])

        objs_to_rotate = [arm]
        if p.export_format == 'FBX' and p.include_mesh:
            objs_to_rotate += [m for m in _iter_meshes_using_armature(arm)]
        originals = _temporarily_rotate_world(objs_to_rotate, p.rot_x, p.rot_y, p.rot_z)

        _select_for_export(arm, include_mesh=(p.include_mesh and p.export_format=='FBX'))
        base = arm.name + ("_" + _safe_filename(act.name) if act else "_POSE")
        filepath = os.path.join(out_dir, f"{base}.{p.export_format.lower()}")

        try:
            if p.export_format == 'FBX':
                bpy.ops.export_scene.fbx(
                    filepath=filepath,
                    use_selection=True,
                    add_leaf_bones=p.add_leaf_bones,
                    bake_anim=True,
                    bake_anim_use_all_bones=True,
                    bake_anim_use_nla_strips=False,
                    bake_anim_use_all_actions=False,
                    bake_anim_force_startend_keying=True,
                    bake_anim_step=1.0,
                    bake_anim_simplify_factor=p.simplify,
                    apply_scale_options='FBX_SCALE_ALL',
                    global_scale=p.export_scale,
                    axis_forward='-Z',
                    axis_up='Y',
                    bake_space_transform=True
                )
            else:
                empty = _apply_bvh_rotation_to_roots(arm, p.rot_x, p.rot_y, p.rot_z)
                bpy.ops.export_anim.bvh(
                    filepath=filepath,
                    frame_start=scene.frame_start,
                    frame_end=scene.frame_end,
                    root_transform_only=False,
                    global_scale=p.export_scale
                )
                _clear_bvh_rotation_helper(empty, arm)
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            _restore_world(originals)
            scene.frame_start, scene.frame_end = orig_fs, orig_fe
            _revert_temp_mapping(inv_map, arm)
            return {'CANCELLED'}

        _restore_world(originals)
        scene.frame_start, scene.frame_end = orig_fs, orig_fe
        _revert_temp_mapping(inv_map, arm)
        self.report({'INFO'}, f"Wrote {filepath}")
        return {'FINISHED'}

class AJ_OT_export_all_actions(Operator):
    bl_idname = "aj.export_all_actions"
    bl_label = "Export All Actions (Per File)"
    bl_description = "Exports each Action as its own FBX/BVH file for the chosen armature"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        arm = p.target_armature if p.export_side == 'TARGET' else p.source_armature
        if not (arm and arm.type == 'ARMATURE'):
            self.report({'ERROR'}, f"Pick a valid {p.export_side.title()} armature.")
            return {'CANCELLED'}

        out_dir = bpy.path.abspath(p.output_dir) if p.output_dir else bpy.path.abspath("//")
        os.makedirs(out_dir, exist_ok=True)

        acts = _gather_actions_for_armature(arm)
        if not acts:
            self.report({'INFO'}, "No actions found; exporting current pose only.")
            acts = [None]

        inv_map = _apply_temp_mapping_if_needed(p, arm)

        scene = bpy.context.scene
        orig_fs, orig_fe = scene.frame_start, scene.frame_end

        exported = 0
        for act in acts:
            ad = arm.animation_data_create()
            old_action = ad.action
            ad.action = act

            if act and act.frame_range:
                scene.frame_start = int(act.frame_range[0])
                scene.frame_end   = int(act.frame_range[1])
            else:
                scene.frame_start = orig_fs
                scene.frame_end   = orig_fe

            objs_to_rotate = [arm]
            if p.export_format == 'FBX' and p.include_mesh:
                objs_to_rotate += [m for m in _iter_meshes_using_armature(arm)]
            originals = _temporarily_rotate_world(objs_to_rotate, p.rot_x, p.rot_y, p.rot_z)

            _select_for_export(arm, include_mesh=(p.include_mesh and p.export_format=='FBX'))

            base = arm.name
            if act:
                base += "_" + _safe_filename(act.name)
            else:
                base += "_POSE"
            filepath = os.path.join(out_dir, f"{base}.{p.export_format.lower()}")

            try:
                if p.export_format == 'FBX':
                    bpy.ops.export_scene.fbx(
                        filepath=filepath,
                        use_selection=True,
                        add_leaf_bones=p.add_leaf_bones,
                        bake_anim=True,
                        bake_anim_use_all_bones=True,
                        bake_anim_use_nla_strips=False,
                        bake_anim_use_all_actions=False,
                        bake_anim_force_startend_keying=True,
                        bake_anim_step=1.0,
                        bake_anim_simplify_factor=p.simplify,
                        apply_scale_options='FBX_SCALE_ALL',
                        global_scale=p.export_scale,
                        axis_forward='-Z',
                        axis_up='Y',
                        bake_space_transform=True
                    )
                else:
                    empty = _apply_bvh_rotation_to_roots(arm, p.rot_x, p.rot_y, p.rot_z)
                    bpy.ops.export_anim.bvh(
                        filepath=filepath,
                        frame_start=scene.frame_start,
                        frame_end=scene.frame_end,
                        root_transform_only=False,
                        global_scale=p.export_scale
                    )
                    _clear_bvh_rotation_helper(empty, arm)
                exported += 1
            except Exception as e:
                self.report({'WARNING'}, f"{p.export_format} export failed for {base}: {e}")

            _restore_world(originals)
            ad.action = old_action

        scene.frame_start, scene.frame_end = orig_fs, orig_fe
        _revert_temp_mapping(inv_map, arm)
        self.report({'INFO'}, f"Exported {exported} file(s) to {out_dir}")
        return {'FINISHED'}

class AJ_OT_export_fbx_all_actions_onefile(Operator):
    bl_idname = "aj.export_fbx_all_actions_onefile"
    bl_label = "Export FBX (All Actions in One File)"
    bl_description = "Exports one FBX with all Actions as separate takes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.aj_props
        if p.export_format != 'FBX':
            self.report({'ERROR'}, "This button is FBX-only. Switch Format to FBX.")
            return {'CANCELLED'}
        arm = p.target_armature if p.export_side == 'TARGET' else p.source_armature
        if not (arm and arm.type == 'ARMATURE'):
            self.report({'ERROR'}, f"Pick a valid {p.export_side.title()} armature.")
            return {'CANCELLED'}

        out_dir = bpy.path.abspath(p.output_dir) if p.output_dir else bpy.path.abspath("//")
        os.makedirs(out_dir, exist_ok=True)

        acts = _gather_actions_for_armature(arm)
        inv_map = _apply_temp_mapping_if_needed(p, arm)

        scene = bpy.context.scene
        orig_fs, orig_fe = scene.frame_start, scene.frame_end
        if acts:
            starts = [a.frame_range[0] for a in acts if a and a.frame_range]
            ends   = [a.frame_range[1] for a in acts if a and a.frame_range]
            if starts and ends:
                scene.frame_start = int(floor(min(starts)))
                scene.frame_end   = int(ceil(max(ends)))

        objs_to_rotate = [arm]
        if p.include_mesh:
            objs_to_rotate += [m for m in _iter_meshes_using_armature(arm)]
        originals = _temporarily_rotate_world(objs_to_rotate, p.rot_x, p.rot_y, p.rot_z)

        _select_for_export(arm, include_mesh=True)

        base = arm.name + "_ALL_ACTIONS"
        filepath = os.path.join(out_dir, f"{base}.fbx")

        try:
            bpy.ops.export_scene.fbx(
                filepath=filepath,
                use_selection=True,
                add_leaf_bones=p.add_leaf_bones,
                bake_anim=True,
                bake_anim_use_all_bones=True,
                bake_anim_use_nla_strips=False,
                bake_anim_use_all_actions=True,
                bake_anim_force_startend_keying=True,
                bake_anim_step=1.0,
                bake_anim_simplify_factor=p.simplify,
                apply_scale_options='FBX_SCALE_ALL',
                global_scale=p.export_scale,
                axis_forward='-Z',
                axis_up='Y',
                bake_space_transform=True
            )
            msg = f"Wrote {filepath} with {len(acts)} action(s) as takes."
        except Exception as e:
            msg = f"FBX export failed: {e}"
            self.report({'ERROR'}, msg)
            scene.frame_start, scene.frame_end = orig_fs, orig_fe
            _restore_world(originals)
            _revert_temp_mapping(inv_map, arm)
            return {'CANCELLED'}

        scene.frame_start, scene.frame_end = orig_fs, orig_fe
        _restore_world(originals)
        _revert_temp_mapping(inv_map, arm)
        self.report({'INFO'}, msg)
        return {'FINISHED'}

# Pose/Object/XRay/Highlight/Autosync

class AJ_OT_pose_mode(Operator):
    bl_idname = "aj.pose_mode"
    bl_label = "Pose Mode"
    bl_options = {'INTERNAL'}
    def execute(self, context):
        p = context.scene.aj_props
        changed = 0
        for obj in (p.source_armature, p.target_armature):
            if obj and obj.type == 'ARMATURE':
                _ensure_active(obj); _ensure_mode(obj, 'POSE'); obj.show_in_front=True; changed += 1
        self.report({'INFO'}, f"Pose mode set on {changed} armature(s).")
        return {'FINISHED'}

class AJ_OT_object_mode(Operator):
    bl_idname = "aj.object_mode"
    bl_label = "Object Mode"
    bl_options = {'INTERNAL'}
    def execute(self, context):
        p = context.scene.aj_props
        changed = 0
        for obj in (p.source_armature, p.target_armature):
            if obj and obj.type == 'ARMATURE':
                _ensure_active(obj); _ensure_mode(obj, 'OBJECT'); changed += 1
        self.report({'INFO'}, f"Object mode set on {changed} armature(s).")
        return {'FINISHED'}

class AJ_OT_toggle_xray_a(Operator):
    bl_idname = "aj.toggle_xray_a"
    bl_label = "Source X-Ray"
    bl_options = {'INTERNAL'}
    def execute(self, context):
        p = context.scene.aj_props
        A = p.source_armature
        if not A or A.type != 'ARMATURE':
            self.report({'ERROR'}, "Pick Source.")
            return {'CANCELLED'}
        A.show_in_front = not A.show_in_front
        self.report({'INFO'}, f"Source X-Ray {'ON' if A.show_in_front else 'OFF'}"); return {'FINISHED'}

class AJ_OT_toggle_xray_b(Operator):
    bl_idname = "aj.toggle_xray_b"
    bl_label = "Target X-Ray"
    bl_options = {'INTERNAL'}
    def execute(self, context):
        p = context.scene.aj_props
        B = p.target_armature
        if not B or B.type != 'ARMATURE':
            self.report({'ERROR'}, "Pick Target.")
            return {'CANCELLED'}
        B.show_in_front = not B.show_in_front
        self.report({'INFO'}, f"Target X-Ray {'ON' if B.show_in_front else 'OFF'}"); return {'FINISHED'}

def _assign_group_to_all(arm, grp):
    if not arm or not grp or not getattr(arm, "pose", None):
        return
    for pb in arm.pose.bones:
        pb.bone_group = grp

class AJ_OT_apply_highlight(Operator):
    bl_idname = "aj.apply_highlight"
    bl_label = "Retarget Source and Target"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        p = context.scene.aj_props
        A = p.source_armature; B = p.target_armature
        if not (A and B and A.type == 'ARMATURE' and B.type == 'ARMATURE'):
            self.report({'ERROR'}, "Pick valid Source and Target."); return {'CANCELLED'}
        _ensure_active(A); _ensure_mode(A, 'POSE')
        _ensure_active(B); _ensure_mode(B, 'POSE')
        ga = _get_or_make_group(A, "Source (Red)", (1.0, 0.2, 0.2))
        gb = _get_or_make_group(B, "Target (Blue)", (0.2, 0.4, 1.0))
        _assign_group_to_all(A, ga); _assign_group_to_all(B, gb)
        self.report({'INFO'}, "Applied Source=Red, Target=Blue groups."); return {'FINISHED'}

class AJ_OT_toggle_autosync(Operator):
    bl_idname = "aj.toggle_autosync"
    bl_label = "Auto-Sync"
    bl_options = {'INTERNAL'}
    _timer = None
    _last_a = ""
    _last_b = ""

    def _find_row_for(self, props, name):
        if not name: return None
        for i, it in enumerate(props.bone_map):
            if name in (it.original_name, it.current_name, it.target_name):
                return i
        return None

    def modal(self, context, event):
        props = context.scene.aj_props
        if not props.auto_sync:
            wm = context.window_manager
            if self._timer: wm.event_timer_remove(self._timer)
            return {'CANCELLED'}
        if event.type == 'TIMER':
            A = props.source_armature; B = props.target_armature
            a = get_active_bone_name(A) if A else None
            b = get_active_bone_name(B) if B else None
            if (a != self._last_a) or (b != self._last_b):
                idx = None
                if a: idx = self._find_row_for(props, a)
                if idx is None and b: idx = self._find_row_for(props, b)
                if idx is not None and idx != props.active_index:
                    props.active_index = idx; redraw_all_view3d(context)
                self._last_a = a; self._last_b = b
        return {'PASS_THROUGH'}

    def execute(self, context):
        props = context.scene.aj_props
        props.auto_sync = not props.auto_sync
        wm = context.window_manager
        if props.auto_sync:
            self._timer = wm.event_timer_add(0.1, window=context.window); wm.modal_handler_add(self)
            self.report({'INFO'}, "Auto-Sync ON"); return {'RUNNING_MODAL'}
        else:
            self.report({'INFO'}, "Auto-Sync OFF"); return {'CANCELLED'}

# Defaults operators
class AJ_OT_load_defaults(Operator):
    bl_idname = "aj.load_defaults"
    bl_label = "Load Defaults"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefs = bpy.context.preferences.addons[__name__].preferences
        p = context.scene.aj_props
        p.export_scale = prefs.default_scale
        p.rot_x = prefs.default_rot_x
        p.rot_y = prefs.default_rot_y
        p.rot_z = prefs.default_rot_z
        self.report({'INFO'}, "Loaded Action Jackson defaults.")
        return {'FINISHED'}

class AJ_OT_save_defaults(Operator):
    bl_idname = "aj.save_defaults"
    bl_label = "Save Defaults"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefs = bpy.context.preferences.addons[__name__].preferences
        p = context.scene.aj_props
        prefs.default_scale = p.export_scale
        prefs.default_rot_x = p.rot_x
        prefs.default_rot_y = p.rot_y
        prefs.default_rot_z = p.rot_z
        self.report({'INFO'}, "Saved Action Jackson defaults.")
        return {'FINISHED'}

# ----------------------
# UI
# ----------------------

class AJ_PT_main(bpy.types.Panel):
    bl_label = "Action Jackson"
    bl_idname = "AJ_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Action Jackson"

    def draw(self, context):
        p = context.scene.aj_props
        lay = self.layout

        # Retarget block
        box = lay.box()
        box.label(text="Retarget: Source ↔ Target (Onigiri-Style)")
        row = box.row(align=True)
        row.prop(p, "source_armature", text="Source")
        row.operator("aj.toggle_xray_a", text="", icon='GHOST_ENABLED')
        row = box.row(align=True)
        row.prop(p, "target_armature", text="Target")
        row.operator("aj.toggle_xray_b", text="", icon='GHOST_ENABLED')

        row = box.row(align=True)
        row.operator("aj.pose_mode", text="Pose Mode")
        row.operator("aj.object_mode", text="Object Mode")

        row = box.row(align=True)
        row.operator("aj.apply_highlight", text="Retarget Source and Target", icon='COLORSET_03_VEC')

        row = box.row(align=True)
        row.operator("aj.capture_pair", text="Capture Pair (Source→Rename)", icon='ADD')
        row.operator("aj.toggle_autosync", text="", icon='REC')

        # Current column behavior
        row = box.row(align=True)
        row.prop(p, "current_armature_side", text="Current Column")
        row.operator("aj.refresh_current", text="Refresh Current", icon='FILE_REFRESH')
        row.operator("aj.revert_marked", text="Revert Current→Original (Marked)", icon='LOOP_BACK')
        row.operator("aj.mark_mapped_colors", text="", icon='COLOR')

        # Headers
        head = box.row(align=True)
        head.prop(p, "mark_all", text="")
        head.label(text="1-Original")
        head.label(text="2-Current")
        head.label(text="3-Rename")

        # Mapping list
        row = box.row()
        row.template_list("AJ_UL_bone_map", "", p, "bone_map", p, "active_index", rows=14)

        row = box.row(align=True)
        row.prop(p, "follow_list_selection", text="Follow Row Selection", toggle=True, icon='RESTRICT_SELECT_OFF')

        # Live follow
        lf = box.box()
        lf.label(text="Live Follow (pose both rigs together)")
        row = lf.row(align=True)
        row.prop(p, "follow_mode", text="Mode")
        row.operator("aj.toggle_follow_a2b", text="Source → Target", depress=p.live_follow_a2b)
        row.operator("aj.toggle_follow_b2a", text="Target → Source", depress=p.live_follow_b2a)
        row.operator("aj.clear_follow", text="", icon='X')

        # Apply (Source first)
        row = box.row(align=True)
        row.operator("aj.apply_to_a", text="Apply to Source (Original→Rename)", icon='CHECKMARK')
        row.operator("aj.apply_to_b", text="Apply to Target (Original→Rename)", icon='CHECKMARK')

        # Save/Load
        row = box.row(align=True)
        row.prop(p, "mapping_path", text="Mapping Path")
        row = box.row(align=True)
        row.operator("aj.bone_map_load", text="Load...")
        row.operator("aj.bone_map_save", text="Save")

        # Exporter
        lay.separator()
        box = lay.box()
        box.label(text="Action Jackson Exporter")
        row = box.row(align=True)
        row.prop(p, "export_side", text="Armature")
        row.prop(p, "export_format", text="Format")
        row = box.row(align=True)
        row.prop(p, "output_dir", text="Output Folder")
        row = box.row(align=True)
        row.prop(p, "rename_on_export", toggle=True)
        row = box.row(align=True)
        row.prop(p, "export_scale")
        row = box.row(align=True)
        row.prop(p, "rot_x"); row.prop(p, "rot_y"); row.prop(p, "rot_z")
        row = box.row(align=True)
        row.operator("aj.load_defaults", text="Load Defaults", icon='IMPORT')
        row.operator("aj.save_defaults", text="Save Defaults", icon='EXPORT')
        if p.export_format == 'FBX':
            row = box.row(align=True)
            row.prop(p, "include_mesh")
            row.prop(p, "add_leaf_bones")
            row = box.row(align=True)
            row.prop(p, "simplify")
            row = box.row(align=True)
            row.operator("aj.export_fbx_all_actions_onefile", text="Export FBX (All Actions in One File)", icon='ANIM')
        else:
            row = box.row(align=True)
            row.label(text="BVH exports armature animation only.", icon='INFO')
        row = box.row(align=True)
        row.operator("aj.export_current_action", text="Export Current Action (Single File)", icon='SEQUENCE')
        row.operator("aj.export_all_actions", text="Export All Actions (Per File)", icon='EXPORT')

# ----------------------
# Registration
# ----------------------

classes = (
    AJ_Prefs,
    BoneMapItem,
    AJ_Props,
    AJ_UL_bone_map,
    AJ_OT_mark_mapped_colors,
    AJ_OT_revert_row,
    AJ_OT_capture_pair,
    AJ_OT_refresh_current,
    AJ_OT_revert_marked,
    AJ_OT_bone_map_save_dialog,
    AJ_OT_bone_map_load_dialog,
    AJ_OT_apply_to_source,
    AJ_OT_apply_to_target,
    AJ_OT_toggle_follow_a2b,
    AJ_OT_toggle_follow_b2a,
    AJ_OT_clear_follow,
    AJ_OT_export_current_action,
    AJ_OT_export_all_actions,
    AJ_OT_export_fbx_all_actions_onefile,
    AJ_OT_pose_mode,
    AJ_OT_object_mode,
    AJ_OT_toggle_xray_a,
    AJ_OT_toggle_xray_b,
    AJ_OT_apply_highlight,
    AJ_OT_toggle_autosync,
    AJ_PT_main,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.aj_props = PointerProperty(type=AJ_Props)

def unregister():
    del bpy.types.Scene.aj_props
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

if __name__ == "__main__":
    register()

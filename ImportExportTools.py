bl_info = {
    'name': 'Import & Export Tools',
    'author': 'Brandon Hohn',
    'version': (1, 0, 6),
    'blender': (4, 0, 0),
    'location': 'View3D > Sidebar > Import & Export Tools',
    'description': 'Batch import objects from .blend files with spacing and collection grouping in a grid layout without modifying source files, export a selected collection to a new .blend file, and delete empty collections.',
    'category': 'Import-Export',
}

import bpy
import os
from mathutils import Vector
from bpy.props import StringProperty, FloatProperty, IntProperty, PointerProperty, EnumProperty
from bpy.types import Operator, Panel
from bpy_extras.io_utils import ExportHelper

# --- ImportCollectionTools.py content (unchanged) ---

class BATCH_OT_import_projects(Operator):
    bl_idname = 'batch_import.execute'
    bl_label = 'Import Projects'
    bl_description = 'Import all .blend files in a folder into the scene, arranging them in a grid to avoid overlap'

    def execute(self, context):
        wm = context.window_manager
        folder = bpy.path.abspath(wm.batch_import_folder)
        if not folder or not os.path.isdir(folder):
            self.report({'ERROR'}, 'Please select a valid folder.')
            return {'CANCELLED'}

        # gather .blend files recursively
        blend_paths = []
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith('.blend'):
                    blend_paths.append(os.path.join(root, f))
        blend_paths.sort()
        if not blend_paths:
            self.report({'WARNING'}, 'No .blend files found in the selected folder.')
            return {'CANCELLED'}

        # phase 1: scan extents per project
        extents = []  # list of (path, minx, miny, width, depth)
        for path in blend_paths:
            existing = set(bpy.data.objects)
            with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
                data_to.objects = data_from.objects
            new_objs = [o for o in bpy.data.objects if o not in existing]

            # compute bounds
            if new_objs:
                minx = miny = float('inf')
                maxx = maxy = float('-inf')
                for obj in new_objs:
                    bbox = getattr(obj, 'bound_box', None)
                    if bbox:
                        for v in bbox:
                            wv = obj.matrix_world @ Vector(v)
                            minx = min(minx, wv.x)
                            maxx = max(maxx, wv.x)
                            miny = min(miny, wv.y)
                            maxy = max(maxy, wv.y)
                    else:
                        wv = obj.matrix_world.translation
                        minx = min(minx, wv.x)
                        maxx = max(maxx, wv.x)
                        miny = min(miny, wv.y)
                        maxy = max(maxy, wv.y)
                width = maxx - minx
                depth = maxy - miny
            else:
                minx = miny = 0.0
                width = depth = 0.0
            extents.append((path, minx, miny, width, depth))

            # cleanup
            for obj in new_objs:
                bpy.data.objects.remove(obj, do_unlink=True)

        # compute max dimensions and padding
        max_w = max(e[3] for e in extents)
        max_d = max(e[4] for e in extents)
        pad_x = wm.batch_import_spacing_x
        pad_y = wm.batch_import_spacing_y
        cols = wm.batch_import_columns

        # determine start origin
        ref = wm.batch_import_ref_obj
        if ref:
            start_x = ref.matrix_world.translation.x
            start_y = ref.matrix_world.translation.y
        else:
            # compute existing scene max
            scene_objs = list(context.scene.objects)
            if scene_objs:
                minx_e = miny_e = float('inf')
                maxx_e = maxy_e = float('-inf')
                for obj in scene_objs:
                    bbox = getattr(obj, 'bound_box', None)
                    if bbox:
                        for v in bbox:
                            wv = obj.matrix_world @ Vector(v)
                            minx_e = min(minx_e, wv.x)
                            maxx_e = max(maxx_e, wv.x)
                            miny_e = min(miny_e, wv.y)
                            maxy_e = max(maxy_e, wv.y)
                    else:
                        wv = obj.matrix_world.translation
                        minx_e = min(minx_e, wv.x)
                        maxx_e = max(maxx_e, wv.x)
                        miny_e = min(miny_e, wv.y)
                        maxy_e = max(maxy_e, wv.y)
                start_x = maxx_e + max_w + pad_x
                start_y = maxy_e + max_d + pad_y
            else:
                start_x = start_y = 0.0

        # phase 2: import and place in grid
        for idx, (path, minx, miny, _, _) in enumerate(extents):
            project_name = os.path.splitext(os.path.basename(path))[0]
            existing_o = set(bpy.data.objects)
            existing_c = set(bpy.data.collections)

            with bpy.data.libraries.load(path, link=False) as (df, dt):
                dt.collections = df.collections
                dt.objects = df.objects
            new_objs = [o for o in bpy.data.objects if o not in existing_o]
            new_colls = [c for c in bpy.data.collections if c not in existing_c]

            # create project collection
            pc = bpy.data.collections.new(project_name)
            context.scene.collection.children.link(pc)

            # top-level collections
            roots = [c for c in new_colls if not any(c.name in p.children for p in new_colls)]
            for c in roots:
                pc.children.link(c)

            # orphan collection
            orp = bpy.data.collections.new(f"{project_name}_orphans")
            pc.children.link(orp)
            coll_objs = set(o for c in new_colls for o in c.objects)
            for o in new_objs:
                if o not in coll_objs:
                    orp.objects.link(o)

            # grid position (positive Y direction)
            if cols > 0:
                col = idx % cols
                row = idx // cols
            else:
                col = idx
                row = 0
            tx = start_x + col * (max_w + pad_x) - minx
            ty = start_y + row * (max_d + pad_y) - miny  # advance positively on Y
            for o in new_objs:
                o.location.x += tx
                o.location.y += ty

        return {'FINISHED'}

# --- ExportPlugin.py content (modified for packing) ---

def get_collections(self, context):
    return [(col.name, col.name, "") for col in bpy.data.collections]

class ExportCollectionOperator(Operator, ExportHelper):
    """Export the selected collection to a new .blend with lowest-Y pivot at origin"""
    bl_idname = "export.collection"
    bl_label = "Export Collection"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext: StringProperty(
        default=".blend",
        options={'HIDDEN'},
    )
    filter_glob: StringProperty(
        default="*.blend",
        options={'HIDDEN'},
    )

    def execute(self, context):
        coll_name = context.scene.export_collection_list
        collection = bpy.data.collections.get(coll_name)
        if not collection:
            self.report({'ERROR'}, f"Collection '{coll_name}' not found")
            return {'CANCELLED'}

        # Ensure the file has a .blend extension
        filepath = bpy.path.ensure_ext(self.filepath, ".blend")
        original_fp = bpy.data.filepath
        if not original_fp:
            self.report({'ERROR'}, "Please save your blend file before exporting a collection.")
            return {'CANCELLED'}

        # --- FIX ATTEMPT: Pack all external data before export ---
        # This helps ensure all textures and other external files are embedded.
        if bpy.data.is_dirty or not bpy.data.is_saved:
            self.report({'WARNING'}, "Saving the current blend file to pack external data. This might take a moment.")
            bpy.ops.wm.save_mainfile() # Ensure file is saved to pack data effectively
        bpy.ops.file.pack_all() # Pack all external data into the .blend file
        self.report({'INFO'}, "External data (including textures) packed into the current blend file.")

        # Gather objects in collection
        objs = list(collection.objects)
        if not objs:
            self.report({'ERROR'}, f"No objects in collection '{coll_name}'")
            return {'CANCELLED'}

        # Gather related datablocks
        datas = [obj.data for obj in objs if getattr(obj, 'data', None)]
        mats = []
        for d in datas:
            for m in getattr(d, 'materials', []):
                if m and m not in mats:
                    mats.append(m)
        images = []
        for m in mats:
            if m.use_nodes and m.node_tree:
                for node in m.node_tree.nodes:
                    img = getattr(node, 'image', None)
                    if img and img not in images:
                        images.append(img)

        # Determine pivot by lowest Y position
        pivot_obj = min(objs, key=lambda o: o.location.y)
        pivot_loc = pivot_obj.location.copy()
        # Store original locations
        orig_locs = {obj: obj.location.copy() for obj in objs}
        # Apply pivot transform
        for obj in objs:
            obj.location -= pivot_loc

        # Create temporary scene for export
        export_scene = bpy.data.scenes.new(name="ExportScene")
        export_scene.collection.children.link(collection)

        # Prepare datablock set
        datablocks = [export_scene, collection] + objs + datas + mats + images
        datablock_set = set(datablocks)
        try:
            bpy.data.libraries.write(filepath, datablock_set)
            self.report({'INFO'}, f"Exported '{coll_name}' pivoted to '{filepath}'")
        except Exception as e:
            self.report({'ERROR'}, str(e))
            # Restore original locations before exit
            for obj, loc in orig_locs.items():
                obj.location = loc
            # Clean up scene
            bpy.data.scenes.remove(export_scene)
            return {'CANCELLED'}

        # Cleanup: remove temporary scene
        bpy.data.scenes.remove(export_scene)
        # Restore original locations
        for obj, loc in orig_locs.items():
            obj.location = loc

        return {'FINISHED'}

# --- New Delete Empty Collections Operator (unchanged) ---

class COLLECTION_OT_delete_empty(Operator):
    bl_idname = "collection.delete_empty"
    bl_label = "Delete Empty Collections"
    bl_description = "Deletes all empty collections except those named 'Collection'"

    def execute(self, context):
        deleted_count = 0
        for collection in list(bpy.data.collections):
            if collection.name != "Collection" and not collection.objects:
                bpy.data.collections.remove(collection)
                deleted_count += 1
        
        if deleted_count > 0:
            self.report({'INFO'}, f"Deleted {deleted_count} empty collection(s).")
        else:
            self.report({'INFO'}, "No empty collections found to delete.")
        return {'FINISHED'}

# --- Combined UI Panel (unchanged) ---

class COLLECTION_PT_main_panel(Panel):
    bl_label = 'Import & Export Tools'
    bl_idname = 'COLLECTION_PT_main_panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Import & Export Tools'

    def draw(self, context):
        layout = self.layout

        # Batch Importer Subsection
        box_import = layout.box()
        box_import.label(text="Batch Importer", icon='IMPORT')
        wm = context.window_manager
        box_import.prop(wm, 'batch_import_folder', text='Folder')
        box_import.prop(wm, 'batch_import_ref_obj', text='Reference Object')
        box_import.prop(wm, 'batch_import_spacing_x', text='Spacing X')
        box_import.prop(wm, 'batch_import_spacing_y', text='Spacing Y')
        box_import.prop(wm, 'batch_import_columns', text='Projects per Row')
        box_import.operator('batch_import.execute', text='Import Projects')

        # Export Collection Subsection
        box_export = layout.box()
        box_export.label(text="Export Collection", icon='EXPORT')
        box_export.prop(context.scene, "export_collection_list")
        box_export.operator(ExportCollectionOperator.bl_idname)

        # Delete Empty Collections Subsection
        box_delete = layout.box()
        box_delete.label(text="Clean Up Collections", icon='TRASH')
        box_delete.operator(COLLECTION_OT_delete_empty.bl_idname, text="Delete Empty Collections")

# --- Combined register and unregister (unchanged) ---

classes = (
    BATCH_OT_import_projects,
    ExportCollectionOperator,
    COLLECTION_OT_delete_empty,
    COLLECTION_PT_main_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.batch_import_folder = StringProperty(name='Folder', subtype='DIR_PATH')
    bpy.types.WindowManager.batch_import_ref_obj = PointerProperty(name='Reference Object', type=bpy.types.Object)
    bpy.types.WindowManager.batch_import_spacing_x = FloatProperty(name='Additional Spacing X', default=0.0)
    bpy.types.WindowManager.batch_import_spacing_y = FloatProperty(name='Additional Spacing Y', default=0.0)
    bpy.types.WindowManager.batch_import_columns = IntProperty(name='Projects per Row', default=0, min=0)
    bpy.types.Scene.export_collection_list = EnumProperty(
        name="Collection",
        description="Choose the collection to export",
        items=get_collections
    )

def unregister():
    del bpy.types.WindowManager.batch_import_folder
    del bpy.types.WindowManager.batch_import_ref_obj
    del bpy.types.WindowManager.batch_import_spacing_x
    del bpy.types.WindowManager.batch_import_spacing_y
    del bpy.types.WindowManager.batch_import_columns
    del bpy.types.Scene.export_collection_list
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == '__main__':
    register()
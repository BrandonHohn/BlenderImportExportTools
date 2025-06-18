bl_info = {
    'name': 'Import & Export Tools',
    'author': 'Brandon Hohn',
    'version': (1, 0, 8),
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

# Define a minimum dimension for imported projects to prevent zero-sized spacing
MIN_PROJECT_DIMENSION = 0.1 # Adjust this value based on typical object scale in your files

# --- ImportCollectionTools.py content (updated for spacing fix) ---

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
                
                # Flag to check if any mesh/volume/hair objects were found
                found_bounding_object = False 

                for obj in new_objs:
                    bbox = getattr(obj, 'bound_box', None)
                    if bbox: # Objects with a valid bounding box (e.g., meshes, volumes, curves)
                        found_bounding_object = True
                        for v in bbox:
                            wv = obj.matrix_world @ Vector(v)
                            minx = min(minx, wv.x)
                            maxx = max(maxx, wv.x)
                            miny = min(miny, wv.y)
                            maxy = max(maxy, wv.y)
                    else: # Objects without a bound_box (e.g., Empties, Cameras, Lights)
                        wv = obj.matrix_world.translation
                        # For objects without a bounding box, ensure they still contribute to min/max
                        # by using their origin, and we'll later apply a minimum dimension if needed.
                        minx = min(minx, wv.x)
                        maxx = max(maxx, wv.x)
                        miny = min(miny, wv.y)
                        maxy = max(maxy, wv.y)
                
                width = maxx - minx
                depth = maxy - miny
                
                # Ensure a minimum dimension if no objects with valid bounding boxes were found,
                # or if the calculated dimension is effectively zero (e.g., only point-like objects).
                # This prevents projects from having zero width/depth, which breaks spacing.
                if not found_bounding_object or width < 0.001: # Check against a small epsilon
                    width = max(width, MIN_PROJECT_DIMENSION)
                if not found_bounding_object or depth < 0.001: # Check against a small epsilon
                    depth = max(depth, MIN_PROJECT_DIMENSION)

            else: # No objects in the project at all
                minx = miny = 0.0
                width = MIN_PROJECT_DIMENSION # Assign minimum dimension to truly empty projects
                depth = MIN_PROJECT_DIMENSION # Assign minimum dimension to truly empty projects
            
            extents.append((path, minx, miny, width, depth))

            # cleanup
            for obj in new_objs:
                bpy.data.objects.remove(obj, do_unlink=True)

        # compute max dimensions and padding
        if not extents: # Handle case where no blend files were found at all after processing
            self.report({'WARNING'}, 'No valid project extents found for import.')
            return {'CANCELLED'}

        max_w = max(e[3] for e in extents)
        max_d = max(e[4] for e in extents)
        
        # Ensure max_w and max_d are at least MIN_PROJECT_DIMENSION even if all projects are tiny/empty
        max_w = max(max_w, MIN_PROJECT_DIMENSION)
        max_d = max(max_d, MIN_PROJECT_DIMENSION)

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
                    else: # Fallback for objects without bound_box in existing scene
                        wv = obj.matrix_world.translation
                        minx_e = min(minx_e, wv.x)
                        maxx_e = max(maxx_e, wv.x)
                        miny_e = min(miny_e, wv.y)
                        maxy_e = max(maxy_e, wv.y)
                
                # If the scene is effectively empty of bounding objects, start from 0
                if maxx_e == float('-inf') or maxy_e == float('-inf'):
                    start_x = 0.0
                    start_y = 0.0
                else:
                    # Start new grid after the existing scene content
                    start_x = maxx_e + max_w + pad_x 
                    start_y = maxy_e + max_d + pad_y # This pushes the entire grid up after existing content
            else:
                start_x = start_y = 0.0

        # phase 2: import and place in grid
        for idx, (path, minx, miny, _, _) in enumerate(extents): # Use the calculated minx/miny, not the original extent width/depth
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
            else: # If columns is 0, place everything in one row (along X axis)
                col = idx
                row = 0
            
            # Calculate target X and Y for this project's origin point (its minX, minY)
            # The calculation shifts the project so its (minX, minY) aligns with the grid position
            target_x = start_x + col * (max_w + pad_x)
            target_y = start_y + row * (max_d + pad_y)

            # Calculate the translation needed for each object
            # The `minx` and `miny` here are from the loaded project's original bounds
            tx = target_x - minx
            ty = target_y - miny
            
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
        if bpy.data.is_dirty or not bpy.data.is_saved:
            self.report({'WARNING'}, "Saving the current blend file to pack external data. This might take a moment.")
            # Use save_as to avoid overwriting if user doesn't want to save main file
            # or ensure they save first. For simplicity, save_mainfile can be used.
            bpy.ops.wm.save_mainfile() 
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

        # Determine pivot by lowest Y position (if objects exist)
        pivot_loc = Vector((0.0, 0.0, 0.0))
        if objs:
            pivot_obj = min(objs, key=lambda o: o.matrix_world.translation.y) # Use world translation for better pivot
            pivot_loc = pivot_obj.matrix_world.translation.copy()
            
        # Store original locations
        orig_locs = {obj: obj.location.copy() for obj in objs}
        # Apply pivot transform by moving objects relative to the pivot
        for obj in objs:
            obj.location -= pivot_loc

        # Create temporary scene for export
        export_scene = bpy.data.scenes.new(name="ExportScene")
        
        # Link collection to the new scene's master collection, not directly to export_scene
        # This creates a copy of the collection, not moving the original.
        temp_collection = bpy.data.collections.new(collection.name)
        export_scene.collection.children.link(temp_collection)
        for obj in objs:
            temp_collection.objects.link(obj)

        # Prepare datablock set for writing
        # Include scene, collection (temp_collection), objects, their mesh data, materials, and images
        datablocks = {export_scene, temp_collection} | set(objs) | set(datas) | set(mats) | set(images)
        
        try:
            bpy.data.libraries.write(filepath, datablocks)
            self.report({'INFO'}, f"Exported '{collection.name}' pivoted to '{filepath}'")
        except Exception as e:
            self.report({'ERROR'}, str(e))
            # Restore original locations before exit
            for obj, loc in orig_locs.items():
                obj.location = loc
            # Clean up temporary collection and scene on failure
            if temp_collection.users == 1: # Only if temp_collection is not linked elsewhere
                bpy.data.collections.remove(temp_collection)
            if export_scene.users == 1: # Only if export_scene is not linked elsewhere
                bpy.data.scenes.remove(export_scene)
            return {'CANCELLED'}

        # Cleanup: remove temporary collection and scene
        if temp_collection.users == 1:
            bpy.data.collections.remove(temp_collection)
        if export_scene.users == 1:
            bpy.data.scenes.remove(export_scene)
        
        # Restore original locations
        for obj, loc in orig_locs.items():
            obj.location = loc

        return {'FINISHED'}

# --- New Delete Empty Collections Operator (UPDATED with error handling fix) ---

class COLLECTION_OT_delete_empty(Operator):
    bl_idname = "collection.delete_empty"
    bl_label = "Delete Empty Collections"
    bl_description = "Deletes all truly empty collections (no objects, no child collections) except the default 'Collection'."

    def execute(self, context):
        deleted_count = 0
        # Create a list of collections to iterate over as you might modify bpy.data.collections
        collections_to_check = list(bpy.data.collections) 
        self.report({'INFO'}, f"Starting empty collection cleanup. Total collections to check: {len(collections_to_check)}")

        for collection in collections_to_check:
            # Store the name BEFORE potential deletion to avoid ReferenceError
            collection_name = collection.name

            # Skip the default "Collection"
            if collection_name == "Collection":
                self.report({'INFO'}, f"  Skipping default 'Collection'.")
                continue

            self.report({'INFO'}, f"Checking collection: '{collection_name}'")
            
            # Check if the collection has any objects directly linked to it
            if not collection.objects:
                self.report({'INFO'}, f"  Collection '{collection_name}' has no objects. Checking for children...")
                # Check if it has any child collections. A collection is not "empty" if it contains other collections.
                if not collection.children:
                    self.report({'INFO'}, f"  Collection '{collection_name}' has no children. Attempting to delete.")
                    try:
                        # Double-check no objects before removal, though 'if not collection.objects' already handles this
                        if not collection.objects: 
                            bpy.data.collections.remove(collection)
                            deleted_count += 1
                            # Use the stored name here
                            self.report({'INFO'}, f"  Successfully deleted '{collection_name}'.")
                        else:
                            # Use the stored name here
                            self.report({'WARNING'}, f"  Collection '{collection_name}' unexpectedly contained objects before deletion attempt; skipping.")
                    except Exception as e:
                        # Use the stored name here
                        self.report({'ERROR'}, f"  Failed to delete '{collection_name}' due to an error: {e}")
                else:
                    self.report({'INFO'}, f"  Collection '{collection_name}' has children; skipping deletion as it's not truly empty.")
            else:
                self.report({'INFO'}, f"  Collection '{collection_name}' contains objects ({len(collection.objects)}); skipping deletion.")
        
        if deleted_count > 0:
            self.report({'INFO'}, f"Finished. Deleted {deleted_count} empty collection(s).")
        else:
            self.report({'INFO'}, "Finished. No truly empty collections found to delete (or they contain other collections).")
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
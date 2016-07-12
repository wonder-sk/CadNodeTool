#-----------------------------------------------------------
# Copyright (C) 2015 Martin Dobias
#-----------------------------------------------------------
# Licensed under the terms of GNU GPL 2
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#---------------------------------------------------------------------

import math

from PyQt4.QtGui import *
from PyQt4.QtCore import *

from qgis.core import *
from qgis.gui import *

class Vertex(object):
    def __init__(self, layer, fid, vertex_id):
        self.layer = layer
        self.fid = fid
        self.vertex_id = vertex_id


class OneFeatureFilter(QgsPointLocator.MatchFilter):
    """ a filter to allow just one particular feature """
    def __init__(self, layer, fid):
        QgsPointLocator.MatchFilter.__init__(self)
        self.layer = layer
        self.fid = fid
    def acceptMatch(self, match):
        return match.layer() == self.layer and match.featureId() == self.fid


def _digitizing_color_width():
    settings = QSettings()
    color = QColor(
      settings.value("/qgis/digitizing/line_color_red", 255, type=int),
      settings.value("/qgis/digitizing/line_color_green", 0, type=int),
      settings.value("/qgis/digitizing/line_color_blue", 0, type=int),
      settings.value("/qgis/digitizing/line_color_alpha", 200, type=int) )
    width = settings.value("/qgis/digitizing/line_width", 1, type=int)
    return color, width


class NodeTool(QgsMapToolAdvancedDigitizing):
    def __init__(self, canvas, cadDock):
        QgsMapToolAdvancedDigitizing.__init__(self, canvas, cadDock)

        self.snap_marker = QgsVertexMarker(canvas)
        self.snap_marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.snap_marker.setColor(Qt.magenta)
        self.snap_marker.setPenWidth(3)
        self.snap_marker.setVisible(False)

        self.edge_center_marker = QgsVertexMarker(canvas)
        self.edge_center_marker.setIconType(QgsVertexMarker.ICON_BOX)
        self.edge_center_marker.setColor(Qt.red)
        self.edge_center_marker.setPenWidth(1)
        self.edge_center_marker.setVisible(False)

        # used only for moving standalone points
        # (there are no adjacent vertices so self.drag_bands is empty in that case)
        self.drag_point_marker = QgsVertexMarker(canvas)
        self.drag_point_marker.setIconType(QgsVertexMarker.ICON_X)
        self.drag_point_marker.setColor(Qt.red)
        self.drag_point_marker.setPenWidth(3)
        self.drag_point_marker.setVisible(False)

        # rubber band for highlight of features on mouse over
        settings = QSettings()
        color, width = _digitizing_color_width()
        self.feature_band = QgsRubberBand(self.canvas())
        self.feature_band.setColor(color)
        self.feature_band.setWidth(5)
        self.feature_band.setVisible(False)
        self.feature_band_source = None   # tuple (layer, fid) or None depending on what is being shown

        self.vertex_band = QgsRubberBand(self.canvas())
        self.vertex_band.setIcon(QgsRubberBand.ICON_CIRCLE)
        self.vertex_band.setColor(color)
        self.vertex_band.setIconSize(15)
        self.vertex_band.reset(QGis.Point)
        self.vertex_band.addPoint(QgsPoint())
        self.vertex_band.setVisible(False)

        self.drag_bands = []
        self.dragging = None
        self.dragging_topo = []
        self.selected_nodes = []  # list of (layer, fid, vid, f)
        self.selected_nodes_markers = []  # list of vertex markers

        self.dragging_rect_start_pos = None    # QPoint if user is dragging a selection rect
        self.selection_rect = None       # QRect in screen coordinates
        self.selection_rect_item = None  # QRubberBand to show selection_rect

        self.mouse_at_endpoint = None   # Vertex instance or None
        self.endpoint_marker_center = None  # QgsPoint or None (can't get center from QgsVertexMarker)
        self.endpoint_marker = QgsVertexMarker(canvas)
        self.endpoint_marker.setIconType(QgsVertexMarker.ICON_BOX)
        self.endpoint_marker.setColor(Qt.red)
        self.endpoint_marker.setPenWidth(1)
        self.endpoint_marker.setVisible(False)

        self.last_snap = None   # Match or None - to stick with previously highlighted feature

        self.cache = {}

    def __del__(self):
        """ Cleanup canvas items we have created """
        self.canvas().scene().removeItem(self.snap_marker)
        self.canvas().scene().removeItem(self.edge_center_marker)
        self.canvas().scene().removeItem(self.drag_point_marker)
        self.canvas().scene().removeItem(self.feature_band)
        self.canvas().scene().removeItem(self.vertex_band)
        self.canvas().scene().removeItem(self.endpoint_marker)
        self.snap_marker = None
        self.edge_center_marker = None
        self.drag_point_marker = None
        self.feature_band = None
        self.vertex_band = None
        self.endpoint_marker = None

    def deactivate(self):
        self.set_highlighted_nodes([])
        QgsMapToolAdvancedDigitizing.deactivate(self)


    def can_use_current_layer(self):
        layer = self.canvas().currentLayer()
        if not layer:
            print "no active layer!"
            return False

        if not isinstance(layer, QgsVectorLayer):
            print "not vector layer"
            return False

        if not layer.isEditable():
            print "layer not editable!"
            return False

        return True

    def topo_editing(self):
        return QgsProject.instance().readNumEntry("Digitizing", "/TopologicalEditing", 0)[0]

    def add_drag_band(self, v1, v2):
        drag_band = QgsRubberBand(self.canvas())

        color, width = _digitizing_color_width()

        drag_band.setColor(color)
        drag_band.setWidth(width)
        drag_band.addPoint(v1)
        drag_band.addPoint(v2)
        self.drag_bands.append(drag_band)

    def clear_drag_bands(self):
        for band in self.drag_bands:
            self.canvas().scene().removeItem(band)
        self.drag_bands = []

        # for the case when standalone point geometry is being dragged
        self.drag_point_marker.setVisible(False)

    def cadCanvasPressEvent(self, e):

        if not self.can_use_current_layer():
            return

        self.set_highlighted_nodes([])   # reset selection

        if e.button() == Qt.LeftButton:
            # accepting action
            if self.dragging:
                self.move_vertex(e)
            else:
                self.start_dragging(e)
                if not self.dragging:
                    # the user may have started dragging a rect to select vertices
                    self.dragging_rect_start_pos = e.pos()
        elif e.button() == Qt.RightButton:
            # cancelling action
            self.cancel_vertex()

    def cadCanvasReleaseEvent(self, e):
        # only handling of selection rect being dragged
        # (everything else is handled in press event)
        if self.selection_rect is not None:
            pt0 = self.toMapCoordinates(self.dragging_rect_start_pos)
            pt1 = self.toMapCoordinates(e.pos())
            map_rect = QgsRectangle(pt0, pt1)
            nodes = []

            # for each editable layer, select nodes
            for layer in self.canvas().layers():
                if not isinstance(layer, QgsVectorLayer) or not layer.isEditable():
                    continue
                layer_rect = self.toLayerCoordinates(layer, map_rect)
                for f in layer.getFeatures(QgsFeatureRequest(layer_rect)):
                    g = f.geometry()
                    for i in xrange(g.geometry().nCoordinates()):
                        pt = g.vertexAt(i)
                        if layer_rect.contains(pt):
                            nodes.append( Vertex(layer, f.id(), i) )

            self.set_highlighted_nodes(nodes)

            self.stop_selection_rect()

        self.dragging_rect_start_pos = None

    def cadCanvasMoveEvent(self, e):

        if not isinstance(e, QgsMapMouseEvent):
            # due to a bug in QGIS, a generated fake QgsMapMouseEvent
            # by advanced digitizing dock will appear here as an invalid
            # QMouseEvent. This QGIS pull request fixes that:
            # https://github.com/qgis/QGIS/pull/3302
            # For now this is just a workaround - ignoring that event
            return

        if self.dragging:
            self.mouse_move_dragging(e)
        elif self.dragging_rect_start_pos:
            # the user may be dragging a rect to select vertices
            if self.selection_rect is None and \
                    (e.pos() - self.dragging_rect_start_pos).manhattanLength() >= 10:
                self.start_selection_rect(self.dragging_rect_start_pos)
            if self.selection_rect is not None:
                self.update_selection_rect(e.pos())
        else:
            self.mouse_move_not_dragging(e)


    def mouse_move_dragging(self, e):
        if e.mapPointMatch().isValid():
            self.snap_marker.setCenter(e.mapPoint())
            self.snap_marker.setVisible(True)
        else:
            self.snap_marker.setVisible(False)

        self.edge_center_marker.setVisible(False)

        for band in self.drag_bands:
            band.movePoint(1, e.mapPoint())

        # in case of moving of standalone point geometry
        if self.drag_point_marker.isVisible():
            self.drag_point_marker.setCenter(e.mapPoint())

        # make sure the temporary feature rubber band is not visible
        self.feature_band.setVisible(False)
        self.feature_band_source = None
        self.vertex_band.setVisible(False)
        self.endpoint_marker_center = None
        self.endpoint_marker.setVisible(False)

    def snap_to_editable_layer(self, e):
        """ Temporarily override snapping config and snap to vertices and edges
         of any editable vector layer, to allow selection of node for editing
         (if snapped to edge, it would offer creation of a new vertex there).
        """

        map_point = self.toMapCoordinates(e.pos())
        tol = QgsTolerance.vertexSearchRadius(self.canvas().mapSettings())
        snap_type = QgsPointLocator.Type(QgsPointLocator.Vertex|QgsPointLocator.Edge)

        snap_layers = []
        for layer in self.canvas().layers():
            if not isinstance(layer, QgsVectorLayer) or not layer.isEditable():
                continue
            snap_layers.append(QgsSnappingUtils.LayerConfig(
                layer, snap_type, tol, QgsTolerance.ProjectUnits))

        snap_util = self.canvas().snappingUtils()
        old_layers = snap_util.layers()
        old_mode = snap_util.snapToMapMode()
        snap_util.setLayers(snap_layers)
        snap_util.setSnapToMapMode(QgsSnappingUtils.SnapAdvanced)
        m = snap_util.snapToMap(map_point)

        # try to stay snapped to previously used feature
        # so the highlight does not jump around at nodes where features are joined
        if self.last_snap is not None:
            filter_last = OneFeatureFilter(self.last_snap.layer(), self.last_snap.featureId())
            m_last = snap_util.snapToMap(map_point, filter_last)
            if m_last.isValid() and m_last.distance() <= m.distance():
                m = m_last

        snap_util.setLayers(old_layers)
        snap_util.setSnapToMapMode(old_mode)

        self.last_snap = m

        return m

    def is_near_endpoint_marker(self, map_point):
        """check whether we are still close to the self.endpoint_marker"""
        if self.endpoint_marker_center is None:
            return False

        dist_marker = math.sqrt(self.endpoint_marker_center.sqrDist(map_point))
        tol = QgsTolerance.vertexSearchRadius(self.canvas().mapSettings())

        geom = self.cached_geometry_for_vertex(self.mouse_at_endpoint)
        vertex_point = geom.asPolyline()[self.mouse_at_endpoint.vertex_id]  # TODO: multilinestring
        dist_vertex = math.sqrt(vertex_point.sqrDist(map_point))

        return dist_marker < tol and dist_marker < dist_vertex

    def is_match_at_endpoint(self, match):
        geom = self.cached_geometry(match.layer(), match.featureId())
        polyline = geom.asPolyline()
        # TODO: multilinestring
        if len(polyline) == 0:
            return False

        if match.vertexIndex() == 0 or match.vertexIndex() == len(polyline)-1:
            return True

    def position_for_endpoint_marker(self, match):
        geom = self.cached_geometry(match.layer(), match.featureId())
        polyline = geom.asPolyline()
        # TODO: multilinestring
        if len(polyline) == 0:
            return

        if match.vertexIndex() == 0:  # first vertex
            pts = (polyline[1], polyline[0])
        else:  # last vertex
            pts = (polyline[-2], polyline[-1])
        dx = pts[1].x() - pts[0].x()
        dy = pts[1].y() - pts[0].y()
        dist = 15 * self.canvas().mapSettings().mapUnitsPerPixel()
        angle = math.atan2(dy, dx)  # to the top: angle=0, to the right: angle=90, to the left: angle=-90
        x = pts[1].x() + math.cos(angle)*dist
        y = pts[1].y() + math.sin(angle)*dist
        return QgsPoint(x, y)

    def mouse_move_not_dragging(self, e):

        if self.mouse_at_endpoint is not None:
            # check if we are still at the endpoint, i.e. whether to keep showing
            # the endpoint indicator - or go back to snapping to editable layers
            map_point = self.toMapCoordinates(e.pos())
            if self.is_near_endpoint_marker(map_point):
                self.endpoint_marker.setColor(Qt.red)
                self.endpoint_marker.update()
                # make it clear this would add endpoint, not move the vertex
                self.vertex_band.setVisible(False)
                return

        # do not use snap from mouse event, use our own with any editable layer
        m = self.snap_to_editable_layer(e)

        # possibility to move a node
        if m.type() == QgsPointLocator.Vertex:
            self.vertex_band.movePoint(m.point())
            self.vertex_band.setVisible(True)
            # if we are at an endpoint, let's show also the endpoint indicator
            # so user can possibly add a new vertex at the end
            if self.is_match_at_endpoint(m):
                self.mouse_at_endpoint = Vertex(m.layer(), m.featureId(), m.vertexIndex())
                self.endpoint_marker_center = self.position_for_endpoint_marker(m)
                self.endpoint_marker.setCenter(self.endpoint_marker_center)
                self.endpoint_marker.setColor(Qt.gray)
                self.endpoint_marker.setVisible(True)
                self.endpoint_marker.update()
            else:
                self.mouse_at_endpoint = None
                self.endpoint_marker_center = None
                self.endpoint_marker.setVisible(False)
        else:
            self.vertex_band.setVisible(False)
            self.mouse_at_endpoint = None
            self.endpoint_marker_center = None
            self.endpoint_marker.setVisible(False)

        # possibility to create new node here
        if m.type() == QgsPointLocator.Edge:
            map_point = self.toMapCoordinates(e.pos())
            edge_center, is_near_center = self._match_edge_center_test(m, map_point)
            self.edge_center_marker.setCenter(edge_center)
            self.edge_center_marker.setColor(Qt.red if is_near_center else Qt.gray)
            self.edge_center_marker.setVisible(True)
            self.edge_center_marker.update()
        else:
            self.edge_center_marker.setVisible(False)

        # highlight feature
        if m.isValid() and m.layer():
            if self.feature_band_source == (m.layer(), m.featureId()):
                return  # skip regeneration of rubber band if not needed
            geom = self.cached_geometry(m.layer(), m.featureId())
            self.feature_band.setToGeometry(geom, m.layer())
            self.feature_band.setVisible(True)
            self.feature_band_source = (m.layer(), m.featureId())
        else:
            self.feature_band.setVisible(False)
            self.feature_band_source = None

    def keyPressEvent(self, e):

        if not self.dragging and len(self.selected_nodes) == 0:
            return

        if e.key() == Qt.Key_Delete:
            e.ignore()  # Override default shortcut management
            self.delete_vertex()
        elif e.key() == Qt.Key_Escape:
            if self.dragging:
                self.cancel_vertex()
        elif e.key() == Qt.Key_Comma:
            self.highlight_adjacent_vertex(-1)
        elif e.key() == Qt.Key_Period:
            self.highlight_adjacent_vertex(+1)

    # ------------

    def cached_geometry(self, layer, fid):
        if layer not in self.cache:
            self.cache[layer] = {}
            layer.geometryChanged.connect(self.on_cached_geometry_changed)
            layer.featureDeleted.connect(self.on_cached_geometry_deleted)

        if fid not in self.cache[layer]:
            f = layer.getFeatures(QgsFeatureRequest(fid)).next()
            self.cache[layer][fid] = QgsGeometry(f.geometry())

        return self.cache[layer][fid]

    def cached_geometry_for_vertex(self, vertex):
        return self.cached_geometry(vertex.layer, vertex.fid)

    def on_cached_geometry_changed(self, fid, geom):
        """ update geometry of our feature """
        layer = self.sender()
        assert layer in self.cache
        if fid in self.cache[layer]:
            self.cache[layer][fid] = QgsGeometry(geom)

    def on_cached_geometry_deleted(self, fid):
        layer = self.sender()
        assert layer in self.cache
        if fid in self.cache[layer]:
            del self.cache[layer][fid]


    def start_dragging(self, e):

        map_point = self.toMapCoordinates(e.pos())
        if self.is_near_endpoint_marker(map_point):
            self.start_dragging_add_vertex_at_endpoint(map_point)
            return True

        m = self.snap_to_editable_layer(e)
        if not m.isValid():
            print "wrong snap!"
            return False

        # activate advanced digitizing dock
        self.setMode(self.CaptureLine)

        # adding a new vertex instead of moving a vertex
        if m.hasEdge():
            # only start dragging if we are near edge center
            map_point = self.toMapCoordinates(e.pos())
            _, is_near_center = self._match_edge_center_test(m, map_point)
            if not is_near_center:
                return False

            self.start_dragging_add_vertex(m)
        else:   # vertex
            self.start_dragging_move_vertex(e.mapPoint(), m)
        return True


    def start_dragging_move_vertex(self, map_point, m):

        assert m.hasVertex()

        geom = self.cached_geometry(m.layer(), m.featureId())

        # start dragging of snapped point of current layer
        self.dragging = Vertex(m.layer(), m.featureId(), m.vertexIndex())
        self.dragging_topo = []

        v0idx, v1idx = geom.adjacentVertices(m.vertexIndex())
        if v0idx != -1:
            layer_point0 = geom.vertexAt(v0idx)
            map_point0 = self.toMapCoordinates(m.layer(), layer_point0)
            self.add_drag_band(map_point0, m.point())
        if v1idx != -1:
            layer_point1 = geom.vertexAt(v1idx)
            map_point1 = self.toMapCoordinates(m.layer(), layer_point1)
            self.add_drag_band(map_point1, m.point())

        if v0idx == -1 and v1idx == -1:
            # this is a standalone point - we need to use a marker for it
            # to give some feedback to the user
            self.drag_point_marker.setCenter(map_point)
            self.drag_point_marker.setVisible(True)

        if not self.topo_editing():
            return  # we are done now

        class MyFilter(QgsPointLocator.MatchFilter):
            """ a filter just to gather all matches within tolerance """
            def __init__(self, tolerance=None):
                QgsPointLocator.MatchFilter.__init__(self)
                self.matches = []
                self.tolerance = tolerance
            def acceptMatch(self, match):
                if self.tolerance is not None and match.distance() > self.tolerance:
                    return False
                self.matches.append(match)
                return True

        # support for topo editing - find extra features
        for layer in self.canvas().layers():
            if not isinstance(layer, QgsVectorLayer) or not layer.isEditable():
                continue

            myfilter = MyFilter(0)
            loc = self.canvas().snappingUtils().locatorForLayer(layer)
            loc.nearestVertex(map_point, 0, myfilter)
            for other_m in myfilter.matches:
                if other_m == m: continue

                other_g = self.cached_geometry(other_m.layer(), other_m.featureId())

                # start dragging of snapped point of current layer
                self.dragging_topo.append( Vertex(other_m.layer(), other_m.featureId(), other_m.vertexIndex()) )

                v0idx, v1idx = other_g.adjacentVertices(other_m.vertexIndex())
                if v0idx != -1:
                    other_point0 = other_g.vertexAt(v0idx)
                    other_map_point0 = self.toMapCoordinates(other_m.layer(), other_point0)
                    self.add_drag_band(other_map_point0, other_m.point())
                if v1idx != -1:
                    other_point1 = other_g.vertexAt(v1idx)
                    other_map_point1 = self.toMapCoordinates(other_m.layer(), other_point1)
                    self.add_drag_band(other_map_point1, other_m.point())


    def start_dragging_add_vertex(self, m):

        assert m.hasEdge()

        self.dragging = Vertex(m.layer(), m.featureId(), (m.vertexIndex()+1,))
        self.dragging_topo = []

        geom = self.cached_geometry(m.layer(), m.featureId())

        # TODO: handles rings correctly?
        v0 = geom.vertexAt(m.vertexIndex())
        v1 = geom.vertexAt(m.vertexIndex()+1)

        map_v0 = self.toMapCoordinates(m.layer(), v0)
        map_v1 = self.toMapCoordinates(m.layer(), v1)

        if v0.x() != 0 or v0.y() != 0:
            self.add_drag_band(map_v0, m.point())
        if v1.x() != 0 or v1.y() != 0:
            self.add_drag_band(map_v1, m.point())

    def start_dragging_add_vertex_at_endpoint(self, map_point):

        assert self.mouse_at_endpoint is not None

        self.dragging = Vertex(self.mouse_at_endpoint.layer, self.mouse_at_endpoint.fid, -self.mouse_at_endpoint.vertex_id-1)
        self.dragging_topo = []

        geom = self.cached_geometry(self.mouse_at_endpoint.layer, self.mouse_at_endpoint.fid)
        v0 = geom.vertexAt(self.mouse_at_endpoint.vertex_id)
        map_v0 = self.toMapCoordinates(self.mouse_at_endpoint.layer, v0)

        self.add_drag_band(map_v0, map_point)

    def cancel_vertex(self):

        # deactivate advanced digitizing
        self.setMode(self.CaptureNone)

        self.dragging = False
        self.clear_drag_bands()

    def match_to_layer_point(self, dest_layer, map_point, match):

        layer_point = None
        # try to use point coordinates in the original CRS if it is the same
        if match and match.hasVertex() and match.layer() and match.layer().crs() == dest_layer.crs():
            try:
                f = match.layer().getFeatures(QgsFeatureRequest(match.featureId())).next()
                layer_point = f.geometry().vertexAt(match.vertexIndex())
            except StopIteration:
                pass

        # fall back to reprojection of the map point to layer point if they are not the same CRS
        if layer_point is None:
            layer_point = self.toLayerCoordinates(dest_layer, map_point)
        return layer_point

    def move_vertex(self, e):

        # deactivate advanced digitizing
        self.setMode(self.CaptureNone)

        drag_layer = self.dragging.layer
        drag_fid = self.dragging.fid
        drag_vertex_id = self.dragging.vertex_id
        geom = QgsGeometry(self.cached_geometry_for_vertex(self.dragging))
        self.cancel_vertex()

        adding_vertex = False
        appending_vertex = False
        if isinstance(drag_vertex_id, tuple):
            adding_vertex = True
            drag_vertex_id = drag_vertex_id[0]
        elif drag_vertex_id < 0:
            adding_vertex = True
            drag_vertex_id = -drag_vertex_id-1
            if drag_vertex_id != 0:
                drag_vertex_id += 1  # adding after last vertex
                appending_vertex = True

        layer_point = self.match_to_layer_point(drag_layer, e.mapPoint(), e.mapPointMatch())

        # add/move vertex
        if appending_vertex:
            # append needs special handling because ordinary insertVertex does not support it
            vid = QgsVertexId(0, 0, drag_vertex_id, QgsVertexId.SegmentVertex)
            geom_tmp = geom.geometry().clone()
            if not geom_tmp.insertVertex(vid, QgsPointV2(layer_point)):
                print "append vertex failed!"
                return
            geom.setGeometry(geom_tmp)
        elif adding_vertex:
            if not geom.insertVertex(layer_point.x(), layer_point.y(), drag_vertex_id):
                print "insert vertex failed!"
                return
        else:
            if not geom.moveVertex(layer_point.x(), layer_point.y(), drag_vertex_id):
                print "move vertex failed!"
                return

        edits = { drag_layer: { drag_fid: geom } }  # dict { layer : { fid : geom } }

        # add moved vertices from other layers
        for topo in self.dragging_topo:
            if topo.layer not in edits:
                edits[topo.layer] = {}
            if topo.fid in edits:
                topo_geom = QgsGeometry(edits[topo.layer][topo.fid])
            else:
                topo_geom = QgsGeometry(self.cached_geometry_for_vertex(topo))

            if topo.layer.crs() == drag_layer.crs():
                point = layer_point
            else:
                point = self.toLayerCoordinates(topo.layer, e.mapPoint())

            if not topo_geom.moveVertex(point.x(), point.y(), topo.vertex_id):
                print "[topo] move vertex failed!"
                continue
            edits[topo.layer][topo.fid] = topo_geom

        # do the changes to layers
        for layer, features_dict in edits.iteritems():
            layer.beginEditCommand( self.tr( "Moved vertex" ) )
            for fid, geometry in features_dict.iteritems():
                layer.changeGeometry(fid, geometry)
            layer.endEditCommand()
            layer.triggerRepaint()


    def delete_vertex(self):

        if len(self.selected_nodes) != 0:
            to_delete = self.selected_nodes
        else:
            adding_vertex = isinstance(self.dragging.vertex_id, tuple)
            to_delete = [self.dragging] + self.dragging_topo
            self.cancel_vertex()

            if adding_vertex:
                return   # just cancel the vertex

        self.set_highlighted_nodes([])   # reset selection

        # switch from a plain list to dictionary { layer: { fid: [vertexNr1, vertexNr2, ...] } }
        to_delete_grouped = {}
        for vertex in to_delete:
            if vertex.layer not in to_delete_grouped:
                to_delete_grouped[vertex.layer] = {}
            if vertex.fid not in to_delete_grouped[vertex.layer]:
                to_delete_grouped[vertex.layer][vertex.fid] = []
            to_delete_grouped[vertex.layer][vertex.fid].append(vertex.vertex_id)

        # main for cycle to delete all selected vertices
        for layer, features_dict in to_delete_grouped.iteritems():

            layer.beginEditCommand( self.tr( "Deleted vertex" ) )
            success = True

            for fid, vertex_ids in features_dict.iteritems():
                res = QgsVectorLayer.Success
                for vertex_id in sorted(vertex_ids, reverse=True):
                    if res != QgsVectorLayer.EmptyGeometry:
                        res = layer.deleteVertexV2(fid, vertex_id)
                    if res != QgsVectorLayer.EmptyGeometry and res != QgsVectorLayer.Success:
                        print "failed to delete vertex!", layer.name(), fid, vertex_id, vertex_ids
                        success = False

            if success:
                layer.endEditCommand()
                layer.triggerRepaint()
            else:
                layer.destroyEditCommand()

        # pre-select next node for deletion if we are deleting just one node
        if len(to_delete) == 1:
            vertex = to_delete[0]
            geom = QgsGeometry(self.cached_geometry_for_vertex(vertex))

            # if next vertex is not available, use the previous one
            if geom.vertexAt(vertex.vertex_id) == QgsPoint():
                vertex.vertex_id -= 1

            if geom.vertexAt(vertex.vertex_id) != QgsPoint():
                self.set_highlighted_nodes([Vertex(vertex.layer, vertex.fid, vertex.vertex_id)])



    def set_highlighted_nodes(self, list_nodes):
        for marker in self.selected_nodes_markers:
            self.canvas().scene().removeItem(marker)
        self.selected_nodes_markers = []

        for node in list_nodes:
            geom = self.cached_geometry_for_vertex(node)
            marker = QgsVertexMarker(self.canvas())
            marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
            #marker.setIconSize(5)
            #marker.setPenWidth(2)
            marker.setColor(Qt.blue)
            marker.setCenter(geom.vertexAt(node.vertex_id))
            self.selected_nodes_markers.append(marker)
        self.selected_nodes = list_nodes

    def highlight_adjacent_vertex(self, offset):
        """Allow moving back and forth selected vertex within a feature"""
        if len(self.selected_nodes) == 0:
            return

        node = self.selected_nodes[0]  # simply use the first one

        geom = self.cached_geometry_for_vertex(node)
        pt = geom.vertexAt(node.vertex_id+offset)
        if pt != QgsPoint():
            node = Vertex(node.layer, node.fid, node.vertex_id+offset)
        self.set_highlighted_nodes([node])


    def start_selection_rect(self, point0):
        """Initialize rectangle that is being dragged to select nodes.
        Argument point0 is in screen coordinates."""
        assert self.selection_rect is None
        self.selection_rect = QRect()
        self.selection_rect.setTopLeft(point0)
        self.selection_rect_item = QRubberBand(QRubberBand.Rectangle, self.canvas())

    def update_selection_rect(self, point1):
        """Update bottom-right corner of the existing selection rectangle.
        Argument point1 is in screen coordinates."""
        assert self.selection_rect is not None
        self.selection_rect.setBottomRight(point1)
        self.selection_rect_item.setGeometry(self.selection_rect.normalized())
        self.selection_rect_item.show()

    def stop_selection_rect(self):
        assert self.selection_rect is not None
        self.selection_rect_item.deleteLater()
        self.selection_rect_item = None
        self.selection_rect = None


    def _match_edge_center_test(self, m, map_point):
        """ Using a given edge match and original map point, find out
         center of the edge and whether we are close enough to the center """
        p0, p1 = m.edgePoints()
        edge_center = QgsPoint((p0.x() + p1.x())/2, (p0.y() + p1.y())/2)

        dist_from_edge_center = math.sqrt(map_point.sqrDist(edge_center))
        tol = QgsTolerance.vertexSearchRadius(self.canvas().mapSettings())
        is_near_center = dist_from_edge_center < tol

        return edge_center, is_near_center

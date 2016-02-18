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

from PyQt4.QtGui import *
from PyQt4.QtCore import *

from qgis.core import *
from qgis.gui import *

def classFactory(iface):
    return CadNodeToolPlugin(iface)


class CadNodeToolPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.current_layer = None

    def initGui(self):
        self.action = QAction("NODE", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)

        self.iface.currentLayerChanged.connect(self.onCurrentLayerChanged)

        self.tool = NodeTool(self.iface.mapCanvas(), self.iface.cadDockWidget())
        self.tool.setAction(self.action)

        self.onCurrentLayerChanged()

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        del self.action
        del self.tool

    def run(self):
        self.iface.mapCanvas().setMapTool(self.tool)

    def onCurrentLayerChanged(self):
        if self.current_layer:
            self.current_layer.editingStarted.disconnect(self.onEditingStartStop)
            self.current_layer.editingStopped.disconnect(self.onEditingStartStop)
        self.action.setEnabled(self.tool.can_use_current_layer())
        self.current_layer = self.iface.mapCanvas().currentLayer()
        if self.current_layer:
            self.current_layer.editingStarted.connect(self.onEditingStartStop)
            self.current_layer.editingStopped.connect(self.onEditingStartStop)

    def onEditingStartStop(self):
        self.action.setEnabled(self.tool.can_use_current_layer())


class NodeTool(QgsMapToolAdvancedDigitizing):
    def __init__(self, canvas, cadDock):
        QgsMapToolAdvancedDigitizing.__init__(self, canvas, cadDock)

        self.snap_marker = QgsVertexMarker(canvas)
        self.snap_marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.snap_marker.setColor(Qt.magenta)
        self.snap_marker.setPenWidth(3)
        self.snap_marker.setVisible(False)

        self.drag_bands = []

        self.dragging = None

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

    def add_drag_band(self, v1, v2):
        drag_band = QgsRubberBand(self.canvas())
        drag_band.setColor(Qt.blue)
        drag_band.setWidth(3)
        drag_band.addPoint(v1)
        drag_band.addPoint(v2)
        self.drag_bands.append(drag_band)

    def clear_drag_bands(self):
        for band in self.drag_bands:
            self.canvas().scene().removeItem(band)
        self.drag_bands = []

    def cadCanvasPressEvent(self, e):

        if not self.can_use_current_layer():
            return

        layer = self.canvas().currentLayer()

        if self.dragging:
            # stop dragging
            drag_layer, drag_fid, drag_vertex_id, drag_f = self.dragging
            self.dragging = False
            self.clear_drag_bands()

            # move vertex
            geom = QgsGeometry(drag_f.geometry())
            if not geom.moveVertex(e.mapPoint().x(), e.mapPoint().y(), drag_vertex_id):
                print "move vertex failed!"
                return
            layer.beginEditCommand( self.tr( "Moved vertex" ) )
            layer.changeGeometry(drag_fid, geom)
            layer.endEditCommand()
            layer.triggerRepaint()
            return

        # TODO: exclude other layers
        m = self.canvas().snappingUtils().snapToMap(e.mapPoint())
        if not m.hasVertex() or m.layer() != layer:
            print "wrong snap!"
            return

        f = layer.getFeatures(QgsFeatureRequest(m.featureId())).next()

        # start dragging of snapped point of current layer
        self.dragging = (m.layer(), m.featureId(), m.vertexIndex(), f)

        # TODO: handle rings
        v0 = f.geometry().vertexAt(m.vertexIndex()-1)
        v1 = f.geometry().vertexAt(m.vertexIndex()+1)

        if v0.x() != 0 or v0.y() != 0:
            self.add_drag_band(v0, m.point())
        if v1.x() != 0 or v1.y() != 0:
            self.add_drag_band(v1, m.point())

    def cadCanvasReleaseEvent(self, e):
        pass

    def cadCanvasMoveEvent(self, e):
        QgsMapToolAdvancedDigitizing.cadCanvasMoveEvent(self, e)

        self.snap_marker.setCenter(e.mapPoint())
        self.snap_marker.setVisible(e.isSnapped())

        if self.dragging:
            for band in self.drag_bands:
                band.movePoint(1, e.mapPoint())

"""A program to quickly browse Klamp't objects.  Run it with

   python resourcebrowser.py

If you know what items to use in the reference world, call

   python resourcebrowser.py world.xml

or 

   python resourcebrowser.py item1 item2 ...

where the items are world, robot, terrain, object, or geometry files.
"""

from klampt import *
from klampt.io import loader,resource
from klampt.math import se3
from klampt.model.trajectory import Trajectory,RobotTrajectory
from klampt.model.multipath import MultiPath
from klampt.model import types
from klampt import vis
from klampt.vis.qtbackend import QtGLWindow
from klampt.vis.glcommon import GLMultiViewportProgram
import sys,os,time
from PyQt4 import QtGui
from PyQt4 import QtCore

world_item_extensions = set(['.obj','.rob','.urdf','.env'])
robot_override_types = ['Config','Configs']
animation_types = ['Trajectory','LinearPath','MultiPath']
create_types = resource.visualEditTypes()[:-1]

def save(obj,fn):
    if hasattr(obj,'saveFile'):
        return obj.saveFile(fn)
    if hasattr(obj,'save'):
        return obj.save(fn)
    type = resource.filenameToType(fn)
    return loader.save(obj,type,fn)

MAX_VIS_ITEMS = 1000
MAX_VIS_CACHE = 10

def copyCamera(cam,camDest):
    camDest.rot = cam.rot[:]
    camDest.tgt = cam.tgt[:]
    camDest.dist = cam.dist


class MyMultiViewportProgram(GLMultiViewportProgram):
    def __init__(self):
        GLMultiViewportProgram.__init__(self)
        self.animating = False
        self.animationTime = 0
        self.animationDuration = 0
        self.animationStartTime = 0
        self.items = dict()
    def startAnim(self):
        self.animating = True
        self.animationStartTime = time.time()
        self.idlesleep(0)
    def stopAnim(self):
        self.animating = False
        self.idlesleep(float('inf'))
    def setAnimTime(self,t):
        self.stopAnim()
        self.animationTime = t
        self._updateTime(t)
    def _updateTime(self,t):
        #print "_updateTime",t
        def clearStartTime(v):
            v.animationStartTime = 0
            for n,subapp in v.subAppearances.iteritems():
                clearStartTime(subapp)
        for (k,item) in self.items.iteritems():
            item.plugin.animationTime(self.animationTime)
            for (k,v) in item.plugin.items.iteritems():
                #do animation updates
                clearStartTime(v)
                v.updateAnimation(t)
        self.refresh()
    def idlefunc(self):
        if not self.animating:
            GLMultiViewportProgram.idlefunc(self)
            return
        t = time.time()
        self.animationTime = t - self.animationStartTime
        if self.animationDuration == 0:
            self.animationTime = 0
        else:
            self.animationTime = self.animationTime % self.animationDuration
            self._updateTime(self.animationTime)

class ResourceItem:
    def __init__(self,obj):
        self.obj = obj
        self.plugin = None
        self.program = None
        self.animationBuddy = None

class ResourceBrowser(QtGui.QMainWindow):
    def __init__(self,glwindow=None,parent=None):
        QtGui.QMainWindow.__init__(self,parent)
         # Splitter to show 2 views in same widget easily.
        self.splitter = QtGui.QSplitter()
        # The model.
        self.model = QtGui.QFileSystemModel()
        # You can setRootPath to any path.
        self.model.setRootPath(QtCore.QDir.rootPath())
        # Add filters
        filters = QtCore.QStringList();
        for k,v in resource.extensionToType.iteritems():
            filters.append("*"+k)
        filters.append("*.xml")
        filters.append("*.json")
        filters.append("*.txt")
        filters.append("*.obj")
        filters.append("*.rob")
        filters.append("*.urdf")
        filters.append("*.env")
        self.model.setNameFilters(filters)

        # Create the view in the splitter.
        self.view = QtGui.QTreeView()
        # Set the model of the view.
        self.view.setModel(self.model)
        #nicer size for columns
        self.view.header().resizeSection(0, 200)
        self.view.header().resizeSection(1, 75)
        self.view.header().resizeSection(2, 75)
        self.view.header().resizeSection(3, 150)
        # Set the root index of the view as the user's home directory.
        #self.view.setRootIndex(self.model.index(QtCore.QDir.homePath()))
        self.view.setRootIndex(self.model.index(os.getcwd()))
        self.view.setSelectionMode(QtGui.QAbstractItemView.ExtendedSelection)

        self.world = WorldModel()
        self.tempWorld = WorldModel()
        self.active = dict()
        self.emptyVisPlugin = vis.VisualizationPlugin()
        self.emptyVisPlugin.add("world",self.world)
        self.emptyVisProgram = None
        self.selected = set()
        self.visCache = []
        self.modified = set()
        
        self.left = QtGui.QFrame()
        self.right = QtGui.QFrame()
        self.leftLayout = QtGui.QVBoxLayout()
        self.left.setLayout(self.leftLayout)

        self.upButton = QtGui.QPushButton("Up")
        self.leftLayout.addWidget(self.upButton)
        self.leftLayout.addWidget(self.view)
        #visualization configuration
        vbuttonLayout = QtGui.QHBoxLayout()
        self.autoFitCameraButton = QtGui.QCheckBox("Auto-fit cameras")
        self.lockCameraCheck = QtGui.QCheckBox("Lock cameras")
        #self.overlayCheck = QtGui.QCheckBox("Overlay")
        self.maxGridItemsLabel = QtGui.QLabel("Grid width")
        self.maxGridItems = QtGui.QSpinBox()
        self.maxGridItems.setRange(3,15)
        self.autoFitCameraButton.setToolTip("If checked, the camera is automatically fit the the items in the scene")
        self.lockCameraCheck.setToolTip("If checked, all cameras are navigated simultaneously")
        #self.overlayCheck.setTooltip("If checked, all items are drawn on top of one another")
        self.maxGridItems.setToolTip("Max # height/width in the visualization pane")
        vbuttonLayout.addWidget(self.autoFitCameraButton)
        vbuttonLayout.addWidget(self.lockCameraCheck)
        #vbuttonLayout.addWidget(self.overlayCheck)
        vbuttonLayout.addWidget(self.maxGridItemsLabel)
        vbuttonLayout.addWidget(self.maxGridItems)
        self.leftLayout.addLayout(vbuttonLayout)

        #playback
        self.timeDriver = QtGui.QSlider()
        self.timeDriver.setOrientation(QtCore.Qt.Horizontal)
        self.timeDriver.setRange(0,1000)
        #self.timeDriver.setSizeHint()
        self.playButton = QtGui.QPushButton("Play")
        self.playButton.setCheckable(True)
        self.stopButton = QtGui.QPushButton("Stop")
        self.playButton.setToolTip("Starts/pauses playing any selected animations")
        self.stopButton.setToolTip("Stops playing any selected animations")
        label = QtGui.QLabel("Time")
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        vbuttonLayout = QtGui.QHBoxLayout()
        vbuttonLayout.addWidget(label)
        vbuttonLayout.addWidget(self.timeDriver)
        vbuttonLayout.addWidget(self.playButton)
        vbuttonLayout.addWidget(self.stopButton)
        self.leftLayout.addLayout(vbuttonLayout)

        #editing
        vbuttonLayout = QtGui.QHBoxLayout()
        self.editButton = QtGui.QPushButton("Edit item")
        self.saveButton = QtGui.QPushButton("Save item")
        self.editButton.setToolTip("Pops up a dialog to edit the selected item, if available")
        self.saveButton.setToolTip("Saves changes to edited items")
        self.saveButton.setEnabled(False)
        self.createComboBox = QtGui.QComboBox()
        self.createComboBox.addItem("Create new item...")
        for n in create_types:
            self.createComboBox.addItem(n)
        vbuttonLayout.addWidget(self.editButton)
        vbuttonLayout.addWidget(self.saveButton)
        vbuttonLayout.addWidget(self.createComboBox)
        self.leftLayout.addLayout(vbuttonLayout)

        #world configuration
        vbuttonLayout = QtGui.QHBoxLayout()
        self.addButton = QtGui.QPushButton("Add to world")
        self.clearButton = QtGui.QPushButton("Clear world")
        self.addButton.setToolTip("Adds the selected item(s) to the reference world")
        self.clearButton.setToolTip("Clears the reference world")
        vbuttonLayout.addWidget(self.addButton)
        vbuttonLayout.addWidget(self.clearButton)
        self.leftLayout.addLayout(vbuttonLayout)
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(self.right)
        self.splitter.setHandleWidth(7)
        self.setCentralWidget(self.splitter)

        self.rightLayout = QtGui.QVBoxLayout()
        self.right.setLayout(self.rightLayout)
        if glwindow is None:
            self.glwidget = QtGLWindow("viewport")
        else:
            self.glwidget = glwindow
        self.rightLayout.addWidget(self.glwidget)
        self.glviewportManager = MyMultiViewportProgram()
        self.glwidget.setProgram(self.glviewportManager)
        self.glwidget.setParent(self.splitter)
        self.glviewportManager.sizePolicy = 'squeeze'
        self.glviewportManager.addView(self.emptyVisPlugin)
        self.glviewportManager.items = self.active
        self.emptyVisProgram = self.glviewportManager.views[-1]
        self.glwidget.setFixedSize(QtGui.QWIDGETSIZE_MAX,QtGui.QWIDGETSIZE_MAX)
        self.glwidget.setSizePolicy(QtGui.QSizePolicy(QtGui.QSizePolicy.Expanding,QtGui.QSizePolicy.Expanding))
        self.glwidget.adjustSize()
        self.glwidget.refresh()

        self.upButton.clicked.connect(self.onUpClicked)
        self.view.connect(self.view.selectionModel(),  
                 QtCore.SIGNAL("selectionChanged(QItemSelection, QItemSelection)"),  
                 self.selection_changed) 
        self.view.doubleClicked.connect(self.onViewDoubleClick)
        self.autoFitCameraButton.clicked.connect(self.onAutoFitCamera)
        self.maxGridItems.valueChanged.connect(self.maxGridItemsChanged)
        self.lockCameraCheck.toggled.connect(self.onLockCamerasToggled)
        self.timeDriver.valueChanged.connect(self.timeDriverChanged)
        self.playButton.toggled.connect(self.togglePlay)
        self.stopButton.clicked.connect(self.stopPlay)
        self.editButton.clicked.connect(self.onEditClicked)
        self.saveButton.clicked.connect(self.onSaveClicked)
        self.createComboBox.currentIndexChanged.connect(self.onCreateIndexChanged)
        self.addButton.clicked.connect(self.onAddClicked)
        self.clearButton.clicked.connect(self.onClearClicked)

    def closeEvent(self,event):
        if len(self.modified) > 0:
            reply = QtGui.QMessageBox.question(self, "Unsaved changes", "Would you like to save changes to " ', '.join(self.modified)+ "?",
                                    QtGui.QMessageBox.Yes|QtGui.QMessageBox.No);
            if reply == QtGui.QMessageBox.Yes:
                self.onSaveClicked()
        vis.show(False)

    def onViewDoubleClick(self):
        indices = self.view.selectedIndexes()
        if len(indices) == 0: return
        item = indices[0]
        name = str(self.model.filePath(item))
        oldselected = self.selected
        self.selected = set([name])
        self.onAddClicked()
        self.selected = oldselected

    def onUpClicked(self):
        currentRoot = self.view.rootIndex()
        self.view.setRootIndex(currentRoot.parent())

    def selection_changed(self,newSelection,deselected):
        print "Selection changed!"
        for i in newSelection.indexes():
            if i.column() == 0:
                fn = str(i.model().filePath(i))
                self.selected.add(fn)
                self.add(fn)
                print "  value:",fn
        print "Deselected:"
        for i in deselected.indexes():
            if i.column() == 0:
                fn = str(i.model().filePath(i))
                self.selected.remove(fn)
                self.remove(fn)
                print "  value:",fn
        self.refresh()

    def onAutoFitCamera(self):
        if self.autoFitCameraButton.isChecked():
            for (k,item) in self.active.iteritems():
                vis.autoFitViewport(item.program.view,[self.world,item.obj])

    def onLockCamerasToggled(self,on):
        self.glviewportManager.broadcast = on
        if on:
            self.lockCameras()

    def lockCameras(self):
        view0 = self.glviewportManager.views[0].view
        cam0 = view0.camera
        for p in self.glviewportManager.views[1:]:
            cam = p.view.camera
            copyCamera(cam0,cam)

    def timeDriverChanged(self,value):
        u = value * 0.001
        animTrajectoryTime = u*self.glviewportManager.animationDuration
        for (k,item) in self.active.iteritems():
            obj = item.obj
            plugin = item.plugin
            if item.animationBuddy is not None:
                path = item.animationBuddy
                if plugin.getItem(path).animation is None:
                    plugin.animate(path,obj,endBehavior='halt')
                else:
                    anim = plugin.getItem(path).animation
            plugin.pauseAnimation(False)
        self.glviewportManager.setAnimTime(animTrajectoryTime)

    def togglePlay(self,value):
        self.animating = value
        self.glviewportManager.refresh()
        if value:
            for (k,item) in self.active.iteritems():
                obj = item.obj
                plugin = item.plugin
                if item.animationBuddy is not None:
                    plugin.animate(item.animationBuddy,obj,endBehavior='halt')
                plugin.pauseAnimation(False)
            self.glviewportManager.startAnim()
            self.glviewportManager.refresh()
        else:
            #pause
            self.glviewportManager.stopAnim()
            #self.idlesleep(float('inf'))

    def stopPlay(self):
        self.playButton.setChecked(False)
        #revert to no animations
        for (k,item) in self.active.iteritems():
            obj = item.obj
            plugin = item.plugin
            if isinstance(obj,(Trajectory,MultiPath)):
                robotpath = ('world',self.world.robot(0).getName())
                plugin.animate(robotpath,None)
            plugin.pauseAnimation()

    def onEditClicked(self):
        if len(self.active) == 0:
            QtGui.QMessageBox.warning(self.splitter,"Invalid item","No item selected, can't edit")
            return
        fn = sorted(self.active.keys())[0]
        def doedit():
            print "Launching resource.edit",fn,"..."
            try:
                obj = resource.edit(name=fn,value=self.active[fn].obj,world=self.world)
            except ValueError as e:
                print "Exception raised during resource.edit:",e
                QtGui.QMessageBox.warning(self.splitter,"Editing not available","Unable to edit item of type "+self.active[fn].obj.__class__.__name__)
                return
            if obj is not None:
                self.active[fn].obj = obj
                #mark it as modified and re-add it to the visualization
                basename = os.path.basename(fn)
                self.active[fn].plugin.add(basename,obj)
                self.modified.add(fn)
                self.saveButton.setEnabled(True)
        QtCore.QTimer.singleShot(0,doedit)

    def onSaveClicked(self):
        for fn in self.modified:
            if not save(self.active[fn].obj,fn):
                print "Error saving file",fn
        self.modified = set()
        self.saveButton.setEnabled(False)
    
    def onCreateIndexChanged(self,item):
        if item == 0: return
        type = create_types[item-1]
        robot = None
        if self.world.numRobots() > 0:
            robot = self.world.robot(0)
        obj = resource.edit("untitled",types.make(type,robot),type=type,world=self.world)
        if obj is not None:
            fn = resource.save(obj,type,directory='')
            if fn is not None:
                self.loadedItem(obj,fn)
        self.createComboBox.setCurrentIndex(0)

    def onAddClicked(self):
        if self.world.numIDs() == 0:
            for name in self.selected:
                if name not in self.active: continue
                copyCamera(self.active[name].program.view.camera,self.emptyVisProgram.view.camera)
                break
        todel = []
        for name in self.selected:
            if name not in self.active: continue
            s = self.active[name].obj
            if isinstance(s,(RobotModel,RigidObjectModel,TerrainModel)):
                self.tempWorld.remove(s)
                self.world.add(s.getName(),s)
                todel.append(name)
            elif isinstance(s,WorldModel):
                for i in xrange(s.numRobots()):
                    self.world.add(s.robot(i).getName(),s.robot(i))
                for i in xrange(s.numRigidObjects()):
                    self.world.add(s.rigidObject(i).getName(),s.rigidObject(i))
                for i in xrange(s.numTerrains()):
                    self.world.add(s.terrain(i).getName(),s.terrain(i))
                for k,item in self.active.iteritems():
                    item.plugin.add("world",self.world)
                todel.append(name)
            elif isinstance(s,(TriangleMesh,PointCloud,GeometricPrimitive)):
                t = self.world.makeTerrain(fn)
                t.geometry().set(Geometry3D(s))
                todel.append(name)
            elif isinstance(s,Geometry3D):
                t = self.world.makeTerrain(fn)
                t.geometry().set(s.clone())
                todel.append(name)
        for name in todel:
            self.remove(name)
        if len(todel) > 0:
            self.refresh()

    def onClearClicked(self):
        self.world = WorldModel()
        self.tempWorld = WorldModel()
        self.active = dict()
        self.visCache = []
        self.emptyVisPlugin.add("world",self.world)
        self.refresh()

    def add(self,fn,openDir=True):
        assert fn not in self.active
        for i,(cfn,citem) in enumerate(self.visCache):
            if cfn == fn:
                print 
                print "PULLED",fn,"FROM CACHE"
                print 
                self.active[fn] = citem
                return True
        if len(self.active) >= MAX_VIS_ITEMS:
            return
        if os.path.isdir(fn):
            if openDir:
                for f in os.listdir(fn):
                    print "Listdir gave",f
                    if f not in ['.','..'] and os.path.splitext(f)[1] != '':
                        self.add(os.path.join(fn,f),openDir=False)
                return True
            else:
                return False
        try:
            type = resource.filenameToType(fn)
        except Exception:
            path,ext = os.path.splitext(fn)
            #print "Extension is",ext
            if ext in world_item_extensions:
                try:
                    worldid = self.tempWorld.loadElement(fn)
                except Exception:
                    QtGui.QMessageBox.warning(self.splitter,"Invalid item","Could not load "+fn+" as a Klamp't world element")
                    return False
                if worldid < 0:
                    QtGui.QMessageBox.warning(self.splitter,"Invalid item","Could not load "+fn+" as a Klamp't world element")
                    return False
                obj = None
                for i in xrange(self.tempWorld.numRobots()):
                    if self.tempWorld.robot(i).getID() == worldid:
                        obj = self.tempWorld.robot(i)
                        break
                for i in xrange(self.tempWorld.numRigidObjects()):
                    if self.tempWorld.rigidObject(i).getID() == worldid:
                        obj = self.tempWorld.rigidObject(i)
                        break
                for i in xrange(self.tempWorld.numTerrains()):
                    if self.tempWorld.terrain(i).getID() == worldid:
                        obj = self.tempWorld.terrain(i)
                        break
                assert obj is not None,"Hmm... couldn't find world id %d in world?"%(worldid,)
                self.loadedItem(fn,obj)
                return True
            else:
                QtGui.QMessageBox.warning(self.splitter,"Invalid item","Could not load file "+fn+" as a known Klamp't type")
                return False
        if type == 'xml':
            #try loading a world
            try:
                world = WorldModel()
                res = world.readFile(fn)
                if not res:
                    try:
                        obj = loader.load('MultiPath',fn)
                    except Exception as e:
                        print "Trying MultiPath load, got exception",e
                        import traceback
                        traceback.print_exc()
                        QtGui.QMessageBox.warning(self.splitter,"Invalid WorldModel","Could not load "+fn+" as a world XML file")
                        return False
                    self.loadedItem(fn,obj)
                    return True
            except IOError:
                QtGui.QMessageBox.warning(self.splitter,"Invalid WorldModel","Could not load "+fn+" as a world XML file")
                return False
            self.loadedItem(fn,world)
            return
        elif type == 'json':
            import json
            f = open(fn,'r')
            jsonobj = json.load(f)
            try:
                obj = loader.fromJson(jsonobj)
            except Exception:
                QtGui.QMessageBox.warning(self.splitter,"Invalid JSON","Could not recognize "+fn+" as a known Klamp't type")
                return False
        else:
            try:
                obj = loader.load(type,fn)
            except Exception as e:
                QtGui.QMessageBox.warning(self.splitter,"Invalid item","Error while loading file "+fn+": "+str(e))
                return False
        self.loadedItem(fn,obj)
        return True

    def loadedItem(self,fn,obj):
        assert fn not in self.active
        item = ResourceItem(obj)
        self.active[fn] = item
        item.plugin = vis.VisualizationPlugin()
        basename = os.path.basename(fn)

        #determine whether it's being animated
        if isinstance(obj,Trajectory) and len(obj.milestones) > 0:
            d = len(obj.milestones[0])
            if self.world.numRobots() > 0 and d == self.world.robot(0).numLinks():
                obj = RobotTrajectory(self.world.robot(0),obj.times,obj.milestones)
                robotpath = ('world',self.world.robot(0).getName())
                item.animationBuddy = robotpath
            elif d == 3:
                item.plugin.add("anim_point",[0,0,0])
                item.animationBuddy = "anim_point"
            elif d == 12:
                item.plugin.add("anim_xform",se3.identity())
                item.animationBuddy = "anim_xform"
            else:
                print "Can't interpret trajectory of length",d
        elif isinstance(obj,MultiPath):
            if self.world.numRobots() > 0:
                robotpath = ('world',self.world.robot(0).getName())
                item.animationBuddy = robotpath

        item.plugin.add("world",self.world)
        item.plugin.add(basename,obj)
        item.plugin.addText("label",basename,(10,10))
        try:
            type = vis.objectToVisType(obj,self.world)
        except:
            type = 'unknown'
        if type in robot_override_types:
            path = ('world',self.world.robot(0).getName())
            item.plugin.hide(path)
        item.plugin.initialize()

    def remove(self,fn,openDir=True):
        if os.path.isdir(fn):
            if openDir:
                for f in os.listdir(fn):
                    if f not in ['.','..'] and os.path.splitext(f)[1] != '':
                        self.remove(os.path.join(fn,f),openDir=False)
            return
        if fn not in self.active:
            return
        if fn in self.modified:
            reply = QtGui.QMessageBox.question(self, "Unsaved changes", "Would you like to save changes to " ', '.join(self.modified)+ "?",
                                    QtGui.QMessageBox.Yes|QtGui.QMessageBox.No);
            if reply == QtGui.QMessageBox.Yes:
                save(self.active[fn],fn)
                self.modified.remove(fn)
        s = self.active[fn]
        del self.active[fn]
        copyCamera(s.program.view.camera,self.emptyVisProgram.view.camera)
        print 
        print "ADDING",fn,"TO CACHE"
        print 
        self.visCache.append((fn,s))
        if len(self.visCache) > MAX_VIS_CACHE:
            self.visCache.pop(0)
        cleartemp = isinstance(s.obj,(RobotModel,RigidObjectModel,TerrainModel))
        if cleartemp:
            for (k,v) in self.active.iteritems():
                if isinstance(v.obj,(RobotModel,RigidObjectModel,TerrainModel)):
                    cleartemp = False
        if cleartemp:
            print "Clearing temp world..."
            self.tempWorld = WorldModel()
    
    def maxGridItemsChanged(self):
        self.refresh()

    def refresh(self):
        self.glviewportManager.clearViews()
        if len(self.active) == 0:
            self.glviewportManager.addView(self.emptyVisProgram)
        else:
            for k in sorted(self.active.keys()):
                item = self.active[k]
                if item.program is not None:
                    item.program.view.w,item.program.view.h = (640,480)
                    self.glviewportManager.addView(item.program)
                else:
                    #new view
                    self.glviewportManager.addView(item.plugin)
                    item.program = self.glviewportManager.views[-1]
                    if self.autoFitCameraButton.isChecked():
                        item.plugin.autoFitViewport(item.program.view,[self.world,item.obj])
                    else:
                        copyCamera(self.emptyVisProgram.view.camera,item.program.view.camera)
                if len(self.glviewportManager.views) >= self.maxGridItems.value()**2:
                    break
            if self.glviewportManager.broadcast: #locking cameras
                self.lockCameras()
        self.glviewportManager.animationDuration = 0
        for (k,item) in self.active.iteritems():
            obj = item.obj
            if isinstance(obj,(Trajectory,MultiPath)):
                self.glviewportManager.animationDuration = max(self.glviewportManager.animationDuration,obj.duration())
                print "Setting animation duration to",self.glviewportManager.animationDuration
        self.glviewportManager.refresh()

if __name__ == '__main__':
    print """===============================================================================
USAGE: python resourcebrowser.py [item1 item2 ...]
where the given items are world, robot, terrain, object, or geometry files.

If no items are given, an empty world is used.  You may add items to the
reference world using the Add to World button.
===============================================================================
"""
    def makefunc(gl_backend):
        browser = ResourceBrowser(gl_backend)
        dw = QtGui.QDesktopWidget()
        x=dw.width()*0.8
        y=dw.height()*0.8
        browser.setFixedSize(x,y)
        for fn in sys.argv[1:]:
            res = browser.world.readFile(fn)
            if not res:
                print "Unable to load model",fn
                print "Quitting..."
                sys.exit(1)
            print "Added",fn,"to world"
        if len(sys.argv) > 1:
            browser.emptyVisPlugin.add("world",browser.world)
        return browser
    vis.customUI(makefunc)
    vis.show()
    vis.spin(float('inf'))
    vis.kill()
    exit(0)

    app = QtGui.QApplication(sys.argv)
    browser = ResourceBrowser()
    for fn in sys.argv[1:]:
        res = browser.world.readFile(fn)
        if not res:
            print "Unable to load model",fn
            print "Quitting..."
            sys.exit(1)
        print "Added",fn,"to world"
    if len(sys.argv) > 1:
        browser.emptyVisPlugin.add("world",browser.world)
    dw = QtGui.QDesktopWidget()
    x=dw.width()*0.8
    y=dw.height()*0.8
    browser.setFixedSize(x,y)
    #browser.splitter.setWindowState(QtCore.Qt.WindowMaximized)
    browser.setWindowTitle("Klamp't Resource Browser")
    browser.show()
    # Start the main loop.
    res = app.exec_()
    sys.exit(res)

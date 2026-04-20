from qgis.core import (QgsProcessingContext, QgsProcessingFeedback, QgsRendererCategory, QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsProcessingAlgorithm, QgsProcessingParameterFeatureSource,
    QgsProcessing, QgsProcessingParameterNumber, QgsProcessingParameterField, QgsProcessingParameterRasterLayer, QgsVectorLayer, QgsCoordinateReferenceSystem, QgsProject, QgsRasterLayer)
from qgis.PyQt.QtGui import QColor
import processing, random

#Define a class so that it can use QgsProcessingAlgorithm stuff
class WTEViewshed(QgsProcessingAlgorithm):
    
    #These are the lookups for the input variables
    ITERATIONS = 'ITERATIONS'
    NEST_LAYER = 'NEST_LAYER'
    NEST_HEIGHT_FIELD = 'NEST_HEIGHT_FIELD'
    NEST_ID_FIELD = 'NEST_ID_FIELD'
    CHM = 'CHM'
    DEM = 'DEM'

    #This part runs first to get the inputs given by the user
    def initAlgorithm(self, config=None):
        
        #Nests input
        self.addParameter(QgsProcessingParameterFeatureSource(
                self.NEST_LAYER,'Nest layer',types=[QgsProcessing.TypeVectorPoint]))
                
        self.addParameter(QgsProcessingParameterNumber(
            self.ITERATIONS, 'Iterations (first try 1 to see if it works, then aim for 50 or more)', type=QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1))

        self.addParameter(QgsProcessingParameterField(
            self.NEST_HEIGHT_FIELD, 'Nest height field', parentLayerParameterName=self.NEST_LAYER, type=QgsProcessingParameterField.Numeric))
        
        self.addParameter(QgsProcessingParameterField(
            self.NEST_ID_FIELD, 'Nest ID field', parentLayerParameterName=self.NEST_LAYER))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.CHM, 'Canopy height model', defaultValue=r"Z:\GIS\DataTas\Canopy Height Model Statewide\As of June 2025\TAS_CHM_2m.tif"))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, 'DEM', defaultValue=r"Z:\GIS\DataTas\Elevation\2m Tas DEM Plus Lidar.tif"))
            
    """
    #############################################################################################
    Bring in the user's input
    """

    #This is the part that runs and actually does the processing work
    def processAlgorithm(self, parameters, context, feedback: QgsProcessingFeedback):
        
        #See how it goes and raise an exception if need be
        try:
            
            #Get the input nests layer
            nestSource = self.parameterAsSource(parameters, self.NEST_LAYER, context)
            if nestSource:
                #The below gets the input layer such that the 'selected features only' feature actually works
                nestLayer = QgsVectorLayer("Point?crs=" + nestSource.sourceCrs().authid(), nestSource.sourceName(), "memory")
                nestLayer.dataProvider().addAttributes(nestSource.fields())
                nestLayer.updateFields()
                nestLayer.dataProvider().addFeatures(list(nestSource.getFeatures()))
            
            #Get the other inputs
            iterations = self.parameterAsInt(parameters, self.ITERATIONS, context)
            nestHeightField = self.parameterAsString(parameters, self.NEST_HEIGHT_FIELD, context)
            nestIDField = self.parameterAsString(parameters, self.NEST_ID_FIELD, context)
            canopyHeightModel = self.parameterAsRasterLayer(parameters, self.CHM, context)
            dem = self.parameterAsRasterLayer(parameters, self.DEM, context)
                 
            compressOptions = 'COMPRESS=LZW|PREDICTOR=2|NUM_THREADS=ALL_CPUS|TILED=YES|BIGTIFF=IF_SAFER'


            """
            ##################################################################################################
            Initial processing
            """

            #Buffer out the nests to get the WTE visibility
            bufferedNests = processing.run("native:buffer", {'INPUT':nestLayer,'DISTANCE':1005,'SEGMENTS':5, 'END_CAP_STYLE':0,'JOIN_STYLE':0,
                'MITER_LIMIT':2,'DISSOLVE':False,'SEPARATE_DISJOINT':False, 'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']

            #Get the extent of these visibilities
            bufferedExtent = processing.run("native:polygonfromlayerextent", 
                {'INPUT':bufferedNests,'ROUND_TO':0,'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']

            #Make sure the the resulting rectangle is snapped to the MGA grid
            snappedGrid = processing.run("native:snappointstogrid", {'INPUT':bufferedExtent,'HSPACING':2,'VSPACING':2,'ZSPACING':0,
                'MSPACING':0,'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']

            #Clip out the canopy height model to only be in the relevant area
            chmClipped = processing.run("gdal:cliprasterbymasklayer", {'INPUT':canopyHeightModel,'MASK':snappedGrid,
                'SOURCE_CRS':None,'TARGET_CRS':QgsCoordinateReferenceSystem('EPSG:28355'),'TARGET_EXTENT':bufferedExtent,'NODATA':None,
                'ALPHA_BAND':False,'CROP_TO_CUTLINE':False,'KEEP_RESOLUTION':False,'SET_RESOLUTION':True,'X_RESOLUTION':2,
                'Y_RESOLUTION':2,'MULTITHREADING':True,'OPTIONS':compressOptions, 'DATA_TYPE':1,
                'EXTRA':'-r cubic -dstnodata None','OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']

            #Clip out the DEM to only be in the relevant area
            demClipped = processing.run("gdal:cliprasterbymasklayer", {'INPUT':dem, 'MASK':snappedGrid,'SOURCE_CRS':None,
                'TARGET_CRS':QgsCoordinateReferenceSystem('EPSG:28355'), 'TARGET_EXTENT':bufferedExtent,'NODATA':None,
                'ALPHA_BAND':False,'CROP_TO_CUTLINE':False,'KEEP_RESOLUTION':False,'SET_RESOLUTION':True,'X_RESOLUTION':2,
                'Y_RESOLUTION':2, 'MULTITHREADING':True,'OPTIONS':compressOptions, 'DATA_TYPE':6,
                'EXTRA':'-r cubic -dstnodata None','OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']

            """
            ##################################################################################################
            Burn out the nest spot
            """

            #Buffer around the nests by 10m
            minorNestBuffer = processing.run("native:buffer", {'INPUT':nestLayer,'DISTANCE':5,'SEGMENTS':5,'END_CAP_STYLE':0,'JOIN_STYLE':0,'MITER_LIMIT':2,
                'DISSOLVE':False,'SEPARATE_DISJOINT':False,'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']

            #Drop the canopy to zero around the nests, so that the trees immediately on top of the nests don't inappropriately interfere with the viewshed
            processing.run("gdal:rasterize_over_fixed_value", {'INPUT':minorNestBuffer,'INPUT_RASTER':chmClipped,'BURN':0,'ADD':False,'EXTRA':''})

            """
            ##################################################################################################
            DEM + canopy
            """

            #Prep to store layers in lists
            allViewsheds = []
            transparentCanopies = []
            demPlusTransparentCanopies = []
                        
            #Run a few iterations of making a transparent canopy
            for x in range(0,iterations):

                #Give values between 0 and 5
                randomRaster = processing.run("native:createrandomuniformrasterlayer", {'EXTENT':demClipped,'TARGET_CRS':QgsCoordinateReferenceSystem('EPSG:28355'),
                    'PIXEL_SIZE':2,'OUTPUT_TYPE':0,'LOWER_BOUND':0,'UPPER_BOUND':5, 'CREATE_OPTIONS':compressOptions,
                    'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']
                
                #If the random raster is 0 (16% prevalence) then don't drop the canopy to 0 (i.e keep the part of the tree)
                #The simulates having a semi transparent forest
                transparentCanopies.append(processing.run("gdal:rastercalculator", {'INPUT_A':chmClipped,'BAND_A':1,
                    'INPUT_B':randomRaster,'BAND_B':1,
                    'FORMULA':'A*numpy.less(B, 1)','NO_DATA':None,'EXTENT_OPT':0,'PROJWIN':None,'RTYPE':6, 'OPTIONS':compressOptions,
                    'EXTRA':'', 'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT'])
                
                #Add the transparent canopies into the DEM
                demPlusTransparentCanopies.append(processing.run("gdal:rastercalculator", {'INPUT_A':demClipped,'BAND_A':1,
                    'INPUT_B':transparentCanopies[x],'BAND_B':1,
                    'FORMULA':'A+B','NO_DATA':None,'EXTENT_OPT':0,'PROJWIN':None,'RTYPE':6, 'OPTIONS':compressOptions,
                    'EXTRA':'', 'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT'])

            """
            ##################################################################################################
            Viewshed calcs
            """

            #Run through each nest
            for viewpointFeature in nestLayer.getFeatures():
                
                #Get the coords of the nest, and its height
                viewpointPoint = viewpointFeature.geometry().asPoint()
                xCoord = viewpointPoint.x()
                yCoord = viewpointPoint.y()
                nestHeight = int(viewpointFeature[nestHeightField])
                
                #Run through each iteration of the transparent canopy
                for x in range(0,iterations):
                
                    #Determine the viewshed across the surface
                    #Adding a random amount of height to the nest will make up for a lack of precision
                    viewshed = processing.run("gdal:viewshed", {'INPUT':demPlusTransparentCanopies[x],'BAND':1,'OBSERVER':str(xCoord)+','+str(yCoord)+' [EPSG:28355]',
                        'OBSERVER_HEIGHT':nestHeight + random.randint(0, 5),'TARGET_HEIGHT':1.8, 'MAX_DISTANCE':1000,'OPTIONS':compressOptions,
                        'EXTRA':'', 'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']
                    
                    #Only count areas of ground (i.e if the nest can see a tree then who cares)
                    viewshedRaster = processing.run("gdal:rastercalculator", {'INPUT_A':viewshed,'BAND_A':1, 'INPUT_B':transparentCanopies[x],'BAND_B':1,
                        'FORMULA':'A*(numpy.less(B, 2))','NO_DATA':None,'EXTENT_OPT':3,'PROJWIN':None,'RTYPE':5, 'OPTIONS':compressOptions,
                        'EXTRA':'', 'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']
                    
                    #For the first run the view will sit by itself
                    if x == 0:
                        viewshedSoFar = processing.run("gdal:rastercalculator", {'INPUT_A':viewshedRaster,'BAND_A':1,
                            'FORMULA':'A','NO_DATA':None,'EXTENT_OPT':0,'PROJWIN':None,'RTYPE':5, 'OPTIONS':compressOptions,
                            'EXTRA':'', 'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']

                    #For each subsequent run, the view numbers will accumulate
                    else:
                        viewshedSoFar = processing.run("gdal:rastercalculator", {'INPUT_A':viewshedSoFar,'BAND_A':1,
                            'INPUT_B':viewshedRaster,'BAND_B':1,
                            'FORMULA':'A+B','NO_DATA':None,'EXTENT_OPT':0,'PROJWIN':None,'RTYPE':5, 'OPTIONS':compressOptions,
                            'EXTRA':'', 'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']
                
                #Convert the visibility to polygons
                polygonized = processing.run("gdal:polygonize", {'INPUT':viewshedSoFar,'BAND':1,'FIELD':'DN','EIGHT_CONNECTEDNESS':False,
                    'EXTRA':'', 'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']
                
                #Give a label based on the nest ID
                labelled = processing.run("native:fieldcalculator", {'INPUT':polygonized,'FIELD_NAME':'NestID','FIELD_TYPE':2,'FIELD_LENGTH':0, 'FIELD_PRECISION':0,
                    'FORMULA':"'" + str(viewpointFeature[nestIDField]) + "'",'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']
                
                #Add the polygons to a list of viewshed layers
                allViewsheds.append(processing.run("native:extractbyexpression", {'INPUT':labelled,'EXPRESSION':'"DN" > 0 ',
                        'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT'])

            """
            ##################################################################################################
            Final steps
            """

            #Add all of the viewsheds together
            mergedViews = processing.run("native:mergevectorlayers", {'LAYERS':allViewsheds,'CRS':None,'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT}, context=context, feedback=feedback)['OUTPUT']
            
            #Tidy up the columns and add in the layer
            mergedViews = processing.run("native:refactorfields", {'INPUT':mergedViews,
                'FIELDS_MAPPING':[{'alias': '','comment': '','expression': '"fid"','length': 0,'name': 'fid','precision': 0,'sub_type': 0,'type': 4,'type_name': 'int8'},
                    {'alias': '','comment': '','expression': '"DN"/(2.55*' + str(iterations)+ ')','length': 0,'name': 'VisPercent','precision': 0,'sub_type': 0,'type': 6,'type_name': 'double precision'},
                    {'alias': '','comment': '','expression': '"NestID"','length': 0,'name': 'NestID','precision': 0,'sub_type': 0,'type': 10,'type_name': 'text'}],
                    'OUTPUT':'TEMPORARY_OUTPUT'}, context=context, feedback=feedback)['OUTPUT']
            QgsProject.instance().addMapLayer(mergedViews) 
             
            #Apply a red opacity ramp based on visibility amount
            visibilityValues = [feature.attribute('VisPercent') for feature in mergedViews.getFeatures() if feature.attribute('VisPercent') is not None]
            uniqueVisibility = sorted(set(visibilityValues))
            categories = []
            for visOpacity in uniqueVisibility:
                fill = QgsFillSymbol.createSimple({'color': '255,0,0,' + str(int(visOpacity*2.55)), 'style': 'solid', 'outline_style': 'no'})
                categories.append(QgsRendererCategory(visOpacity, fill, str(visOpacity)))
            visibilityRenderer = QgsCategorizedSymbolRenderer('VisPercent', categories)
            mergedViews.setRenderer(visibilityRenderer)
            mergedViews.triggerRepaint()

        except BaseException as e:
            feedback.reportError(str(e))
        
        #Return nothing because you have to return something
        return {}
    """
    ###############################################################
    Final definitions of names etc
    """

    def name(self):
        return 'wte_viewshed'

    def displayName(self):
        return 'WTE Viewshed'

    def group(self):
        return 'NB Custom Scripts'

    def groupId(self):
        return 'nbcustomscripts'

    def createInstance(self):
        return WTEViewshed()

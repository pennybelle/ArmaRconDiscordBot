#pip install matplotlib

import matplotlib.pyplot as plt
import ast
import os
from datetime import datetime
import json
import logging
import re
from collections import deque
import collections
import traceback
import sys
import itertools
import asyncio
import inspect


class Event_Handler(object):
    def __init__(self, events):
        self.events = events
        self.events.append("other")
        self.Events = []
        
    def add_Event(self, name: str, func):
        if(name in self.events):
            self.Events.append([name,func])
        else:
            raise Exception("Failed to add unknown event: "+name)

    def check_Event(self, parent, *args):
        for event in self.Events:
            func = event[1]
            if(inspect.iscoroutinefunction(func)): #is async
                if(event[0]==parent):
                    if(len(args)>0):
                        asyncio.ensure_future(func(*args))
                    else:
                        asyncio.ensure_future(func())
            else:
                if(event[0]==parent):
                    if(len(args)>0):
                        func(*args)
                    else:
                        func()


class ProcessLog:
    def __init__(self, readLog, cfg_jmw):
        self.readLog = readLog
        self.path = os.path.dirname(os.path.realpath(__file__))
        self.cfg_jmw = cfg_jmw
        
        self.maxDataRows = 10000
        self.dataRows=deque(maxlen=self.maxDataRows)
        self.databuilder = {}
        self.active = False
        
        self.define_EH()
        #Add EventHandlers:
        self.readLog.EH.add_Event("other", self.processLogLine)
        
        self.readLog.pre_scan()
        self.active = True
    
    def define_EH(self):
        self.events = [
            "on_missionHeader",
            "on_missionGameOver",
            "on_missionData"
        ]
        
        self.EH = Event_Handler(self.events)
        
    # index: 0 = current game
    # start = index is starts searching from
    # returns false if not enough data to read log was present
    def getGameEnd(self, start, index = None):
        if(index == None):
            index = 0
        ends = 0
        if(index == 0):
            return start
        for i in range(start, 0, -1):
            try:
                if(self.dataRows[i]["CTI_DataPacket"] == "GameOver"):
                    ends += 1
                    if(ends >= index):
                        return i
            except: #IndexError
                pass
        return False
    

    def getGameData(self, start, index=None):
        if(index == None):
            index = 0
        #due to async scanning and the nature of deque,
        #we need to make sure that the index of elements do not change while generating the game
        #to do that we free one space in the queue
        dl = len(self.dataRows)
        if(dl>=self.maxDataRows):
            self.dataRows.popleft()
        #now we get the postion of our game in the queue
        end = self.getGameEnd(dl, index)
        if(end==False):
            raise EOFError("Failed generating game #{}. End not found".format(index))
        start = self.getGameEnd(dl, index+1)
        if(start==False):
            raise EOFError("Failed generating game #{}. Start not found".format(index))
        return list(collections.deque(itertools.islice(self.dataRows, start+1, end+1)))
  
    def processGameData(self, pdata):
        data = pdata.copy()
        last_time = 0
        last_time_iter = 0
        first_line = True
        set_new = False     #when game crashed and mission continues
        #created_df = False
        #values
        meta = {
                "map": "Unkown",
                "winner": "currentGame",
                "timestamp": str(datetime.now().strftime("%H-%M-%S")),
                "date": str(datetime.now().strftime("%Y-%m-%d")) #TODO get log date
        }
        for val in data:
            if(val["CTI_DataPacket"]=="Header"):
                if(first_line==False):
                    set_new = True   
                meta["map"] = val["Map"]
                
            if(val["CTI_DataPacket"]=="Data"):
                if(set_new == True):
                    set_new = False
                    last_time = last_time_iter

                val["time"] = val["time"]+last_time
                last_time_iter = val["time"] 
                #if(val["time"] > 100000 and created_df == False):
                #    print("[WARNING] Data timeframe out of bounds: {}".format(val))
                #    with open('dataframe.json', 'a+') as outfile:
                #        json.dump(data, outfile)
                #    created_df = True
            if(val["CTI_DataPacket"]=="GameOver"):
                meta["timestamp"] = val["timestamp"]
                #meta["map"] = val["Map"]
                if(val["Lost"]):
                    if(val["Side"] == "WEST"):
                        meta["winner"] = "EAST"
                    else:
                        meta["winner"] = "WEST"
                else:
                    if(val["Side"] == "WEST"):
                        meta["winner"] = "WEST"
                    else:
                        meta["winner"] = "EAST"  
                last_time = 0
                last_time_iter = 0
            first_line = False
        return [meta, data]
    
    #might fail needs try catch
    #uses recent data entries to create a full game
    def readData(self, admin, gameindex):
        meta, game = self.generateGame(len(self.dataRows), gameindex)
        return self.dataToGraph(meta, game, admin)
     
    #generates a game from recent entries    
    # index: 0 = current game
    def generateGame(self, start=None, index=None):
        if(index == None):
            index = 0
        if(start==None):
            start = len(self.dataRows)
        data = self.getGameData(start, index)
        meta, pdata = self.processGameData(data)
        return [meta, pdata]
        
        
    def updateDicArray(self, parent, data):
        if("players" in parent and "players" in data):
            parent["CTI_DataPacket"] = data["CTI_DataPacket"]
            parent["players"] = parent["players"]+data["players"]
            return parent
        parent.update(data)
        return parent
    
    # Conforms a log line array (string) into a valid Python dict
    def parseLine(self, msg):
        r = msg.rstrip() #remove /n
        #converting arma3 boolen working with python +converting rawnames to strings:#
        #(?<!^|\]|\[)"(?!\]|\[$)
        #(?:^(?<!\])|(?<!\[))"(?:(?!\])|\[)
        r = r.replace('\\', '') #Filter possible escape chars
        r = re.sub(r'(?:^(?<!\])|(?<!\[|,))"(?:(?!\]|,))', "'", r) #removes invalid qoutes
        r = r.replace('""', ',"WEST"]')
        r = r.replace(",WEST]", ',"WEST"]')
        r = r.replace(",EAST]", ',"EAST"]') #this still needs working
        r = r.replace("true", "True")
        r = r.replace("false", "False")
        data = ast.literal_eval(r) #WARNING: Security risk
        #print(data)
        return dict(data)
            
    def processLogLine(self, timestamp, line):
        if(self.active==None):
            self.active=False
        #check if line contains a datapacket
        m = re.match('^(\[\["CTI_DataPacket","(.*?)"],.*])', line)
        if(m):
            type = m.group(2)
            try:
                datarow = self.parseLine(line) #convert string into array object
                if(type == "Header"):
                    datarow["timestamp"] = timestamp
                    self.dataRows.append(datarow)
                    if(self.active):
                        self.EH.check_Event("on_missionHeader", datarow)
                if("Data_" in type):
                    index = int(re.match('.*_([0-9]*)', type).group(1))
                    if(len(self.databuilder)>0):
                        index_db = int(re.match('.*_([0-9]*)', self.databuilder["CTI_DataPacket"]).group(1))
                        #check if previous 'Data_x' is present
                        if(index_db+1 == index):
                            self.databuilder = self.updateDicArray(self.databuilder, datarow)
                            #If last element "Data_EOD" is present, 
                            if("EOD" in type):
                                self.databuilder["CTI_DataPacket"] = "Data"
                                self.dataRows.append(self.databuilder.copy())
                                if(self.active):
                                    self.EH.check_Event("on_missionData", self.databuilder.copy())
                                self.databuilder = {}
                    elif(type == "Data_1"):
                        #add first element
                        self.databuilder = self.updateDicArray(self.databuilder, datarow)

                if(type == "EOF"):
                    pass
                    #raise Exception("Read mission EOF")
                    #self.dataRows.append(datarow) #Append EOF (should usually never be called)
                if(type == "GameOver"):
                    datarow["timestamp"] = timestamp #finish time
                    self.dataRows.append(datarow) #Append Gameover / End
                    if(self.active):
                        self.EH.check_Event("on_missionGameOver", datarow)
                
            except Exception as e:
                print(e)
                print(line)
                line = "Error"
                traceback.print_exc()
        #return self.databuilder
                
   
###################################################################################################
#####                                  Graph Generation                                        ####
###################################################################################################   

    def featchValues(self, data,field):
        list = []
        for item in data:
            if(field in item):
                list.append(item[field])
        return list
   
        
    def dataToGraph(self, meta, data, admin):
        lastwinner = meta["winner"]
        lastmap = meta["map"]
        timestamp = meta["timestamp"]
        fdate = meta["date"]
        
        #register plots
        plots = []
        v1 = self.featchValues(data, "score_east")
        v2 = self.featchValues(data, "score_west")
        #data: [[data, color_String],....]
        if(len(v1) > 0):
            plots.append({
                "data": [[v1, "r"],
                        [v2, "b"]],
                "xlabel": "Time in min",
                "ylabel": "Team Score",
                "title": "Team Score"
                })
     
        v1 = self.featchValues(data, "town_count_east")
        v2 = self.featchValues(data, "town_count_west")
        if(len(v1) > 0):
            plots.append({
                "data": [[v1, "r"],
                        [v2, "b"]],
                "xlabel": "Time in min",
                "ylabel": "Towns owned",
                "title": "Towns owned"
                })
                
        v1 = self.featchValues(data, "player_count_east")
        v2 = self.featchValues(data, "player_count_west")
        if(len(v1) > 0):
            plots.append({
                "data": [[v1, "r"],
                        [v2, "b"]],
                "xlabel": "Time in min",
                "ylabel": "Players",
                "title": "Players on Server"
                })  
                
        if(admin == True):
            v1 = self.featchValues(data, "fps")
            if(len(v1) > 0):
                plots.append({
                    "data": [[v1, "g"]],
                    "xlabel": "Time in min",
                    "ylabel": "Server FPS",
                    "title": "Server FPS"
                    }) 
                    
        if(admin == True):       
            v1 = self.featchValues(data, "self.active_SQF_count")
            if(len(v1) > 0):
                plots.append({
                    "data": [[v1, "g"]],
                    "xlabel": "Time in min",
                    "ylabel": "self.active SQF",
                    "title": "self.active Server SQF"
                    })  
                    
        if(admin == True):       
            v1 = self.featchValues(data, "self.active_towns")
            if(len(v1) > 0):
                plots.append({
                    "data": [[v1, "g"]],
                    "xlabel": "Time in min",
                    "ylabel": "self.active Towns",
                    "title": "self.active Towns"
                    }) 
                    
        if(admin == True):       
            v1 = self.featchValues(data, "self.active_AI")
            if(len(v1) > 0):
                plots.append({
                    "data": [[v1, "g"]],
                    "xlabel": "Time in min",
                    "ylabel": "Units",
                    "title": "Total Playable units count"
                    })  
                    
        if(admin == True):       
            v1 = self.featchValues(data, "total_objects")
            if(len(v1) > 0):
                plots.append({
                    "data": [[v1, "g"]],
                    "xlabel": "Time in min",
                    "ylabel": "Objects",
                    "title": "Total Objects count"
                    })  

        #Calculate time in min
        time = self.featchValues(data, "time")
        for i in range(len(time)):
            if(time[i] > 0):
                time[i] = time[i]/60 #seconds->min
        if (len(time) > 0):
            gameduration = round(time[-1])
        else:
            gameduration = 0
        print(timestamp+","+lastwinner+","+str(gameduration))
        
        #maps plot count to image size
        #plot_count: image_size
        hight={ 12: 22,
                11: 22,
                10: 18,
                9: 18,
                8: 14,
                7: 14,
                6: 10,
                5: 10,
                4: 6,
                3: 6,
                2: 3,
                1: 3}
        phight = 10
        if(len(plots) in hight):
            phight = hight[len(plots)]
        fig = plt.figure(figsize = (10,phight)) 

        fig.suptitle("Game end: "+fdate+" "+timestamp+", "+str(gameduration)+"min. Map: "+lastmap+" Winner: "+lastwinner, fontsize=14)
        #red_patch = mpatches.Patch(color='red', label='The red data')
        #plt.legend(bbox_to_anchor=(0, 0), handles=[red_patch])
        fig.subplots_adjust(hspace=0.3)
        zplots = []
        #writes data to plot
        for pdata in plots:
            if(len(pdata["data"][0])>0):
                zplots.append(fig.add_subplot( int(round((len(plots)+1)/2)), 2 ,len(zplots)+1))
                for row in pdata["data"]:
                    zplots[-1].plot(time, row[0], color=row[1])
                zplots[-1].grid(True, lw = 1, ls = '-', c = '.75')
                zplots[-1].set_xlabel(pdata["xlabel"])
                zplots[-1].set_ylabel(pdata["ylabel"])
                zplots[-1].set_title(pdata["title"])
        
        #create folders to for images / raw data
        if not os.path.exists(self.cfg_jmw['data_path']):
            os.makedirs(self.cfg_jmw['data_path'])
        if not os.path.exists(self.cfg_jmw['image_path']):
            os.makedirs(self.cfg_jmw['image_path'])
        
        t=""
        if(lastwinner=="currentGame"):
            t = "-CUR"
        if(admin==True):
            t +="-ADV"
                        #path / date # time # duration # winner # addtional_tags
        filename_pic =(self.cfg_jmw['image_path']+fdate+"#"+timestamp.replace(":","-")+"#"+str(gameduration)+"#"+lastwinner+"#"+lastmap+"#"+t+'.png').replace("\\","/")
        filename =    (self.cfg_jmw['data_path']+ fdate+"#"+timestamp.replace(":","-")+"#"+str(gameduration)+"#"+lastwinner+"#"+lastmap+"#"+t+'.json').replace("\\","/")
        
        #save image
        fig.savefig(filename_pic, dpi=100, pad_inches=3)
        #fig.gcf()
        plt.close('all')
        #save rawdata
        with open(filename, 'w') as outfile:
            json.dump(data, outfile)
        
        return {"date": fdate, "time": timestamp, "lastwinner": lastwinner, "gameduration": gameduration, "picname": filename_pic, "dataname": filename, "data": data}



from flask import Flask,render_template,jsonify,request,abort
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import sqlite3
import operator
import re
import csv
import json
import pika
import uuid
import time
from datetime import datetime
from kazoo.client import KazooClient

zk = KazooClient(hosts='zoo:2181')
zk.start()
zk.ensure_path("/workers")

g=0 							    #flag

app=Flask(__name__)


class ReadDBClient(object):

	def __init__(self):
		self.connection = pika.BlockingConnection(
			pika.ConnectionParameters(host='rabbitmq'))

		self.channel = self.connection.channel()

		result = self.channel.queue_declare(queue='responseQ', durable=True)
		self.callback_queue = result.method.queue

		self.channel.basic_consume(
			queue=self.callback_queue,
			on_message_callback=self.on_response,
			auto_ack=True)

	def on_response(self, ch, method, props, body):
		print("inside response \n")
		print(body)
		if self.corr_id == props.correlation_id:
			self.response = body
			ch.basic_ack(delivery_tag=method.delivery_tag)

	def call(self, data):
		self.response = None
		self.corr_id = str(uuid.uuid4())
		self.channel.queue_declare(queue='readQ',durable=True)
		self.channel.basic_publish(
			exchange='',
			routing_key='readQ',
			properties=pika.BasicProperties(
				reply_to=self.callback_queue,
				correlation_id=self.corr_id,
			),
			body=data)
		while self.response is None:
			self.connection.process_data_events()
		return json.loads(self.response)



@zk.ChildrenWatch("/workers")
def f(ch):
	print("Watch triggered")
	print(ch)

	m=0
	if len(ch)==0:
		print("no worker")
		return
	#Check if master exists
	for c in ch:
		d,s=zk.get("/workers/"+c)
		d = d.decode('utf-8')
		role = d.split(";")[2].strip()
		if(role=="master"):
			m=1
			print("master exists")
			break
	#Making the node with smallest PID as master
	print(m)
	if(m==0):
		min_pid = float("inf")
		min_cid = ""
		cr = ""
		for c in ch:
			d,s = zk.get("/workers/"+c)
			d = d.decode('utf-8')
			cid = d.split(";")[0]
			pid = d.split(";")[1]
			role = d.split(";")[2]
			if(int(pid) < min_pid):
				min_pid = int(pid)
				min_cid = cid
				cr = c
		print(cr+" is the master")
		strin = cid+";"+pid+";"+"master"
		zk.set("/workers/"+cr,strin.encode('utf-8'))
		#create_slave()

	url = "http://0.0.0.0:80/api/v1/count"
	response = requests.get(url=url)
	count = int(response.text)
	cur_slave = len(ch)-1
	total_slave = int((count-1)/20) + 1
	if(total_slave==0):
		total_slave=1
	dif_slave = total_slave - cur_slave
	if(dif_slave>0):
		for i in range(dif_slave):
			create_slave()







#increment number of read requests
def increment():
	f=open("count.txt", "r")
	count = f.read()
	f.close()
	count = int(count)
	count=count+1
	f=open("count.txt", "w")
	f.write(str(count))
	f.close()

#reset read request count every 2 minutes
def reset_count():
	f=open("count.txt", "w")
	count=0
	f.write(str(count))
	f.close()

#File to store all SQL commands
def write_to_file(command):
	with open('commands.txt','a') as fd:
		c = command+'\n'
		fd.write(c)

#To spawn new worker
def create_slave():
	url='http://172.17.0.1:5555/containers/create'
	obj = {"image":"worker"}
	cont_create = requests.post(url, json=obj)
	print(cont_create)
	cont_id = str(cont_create.json()["Id"])
	url = 'http://172.17.0.1:5555/networks/dbaas_default/connect'
	obj = {"Container":cont_id}
	net_add = requests.post(url, json=obj)
	print(net_add)
	url = 'http://172.17.0.1:5555/containers/'+cont_id+'/start'
	run_cont = requests.post(url)
	url = 'http://172.17.0.1:5555/containers/'+cont_id+'/top'
	resp = requests.get(url)
	res = resp.json()

#Kill a worker
def kill_slave(contid):
	url = 'http://172.17.0.1:5555/containers/'+contid+'?force=true'
	res = requests.delete(url)
	print(res)
	url = 'http://172.17.0.1:5555/containers/prune'
	res = requests.post(url)
	

#This function is called every 2 minutes to scale up/down
def check_func():
	url = "http://0.0.0.0:80/api/v1/count"
	response = requests.get(url=url)
	count = int(response.text)
	reset_count()
	children = zk.get_children("/workers")
	cur_slave = len(children)-1
	total_slave = int((count-1)/20) + 1
	if(total_slave==0):
		total_slave=1
	dif_slave = total_slave - cur_slave
	if(dif_slave>0):
		for i in range(dif_slave):
			create_slave()
	elif(dif_slave<0):
		print("KILL")
		dif_slave = dif_slave*(-1)
		container_ids = {}
		for c in children:
			d,s = zk.get("/workers/"+c)
			d = d.decode('utf-8')
			cid = d.split(";")[0]
			pid = d.split(";")[1]
			role = d.split(";")[2]
			if(role=="slave"):
				container_ids[cid]=int(pid)
		container_ids = dict(sorted(container_ids.items(), key=operator.itemgetter(1),reverse=True))
		print(container_ids)
		y = list(container_ids.keys())
		print("Y=",y)
		xx = y[:dif_slave]
		for i in xx:
			print(xx)
			kill_slave(i)
			del container_ids[i]



@app.route('/')
def hello_world():
	return "Orchestrator API"

#Called by worker when it is spawned
@app.route('/getsqlcmd',methods=["GET"])
def get_cmd():
	f = open("commands.txt", "r")
	cmd = f.readlines()
	cmd = [line.strip('\n') for line in cmd]
	if(len(cmd)==0):
		return jsonify({}), 204
	return jsonify(cmd), 200


@app.route('/api/v1/count',methods=["GET"])
def return_count():
	f=open("count.txt", "r")
	count = f.read()
	f.close()
	count=int(count)
	return str(count),200


@app.route('/api/v1/db/write',methods=["POST"])
def write_db():
	data = request.get_json()
	sql=""
	table=data["table"]
	if(data["command"]=="insert"):
		cols = data["column_list"]
		s=""
		for i in cols:
			s=s+i+","
		s=s[:len(s)-1]
		t=""
		for i in cols:
			t=t+cols[i]+","
		t=t[:len(t)-1]
		sql = "insert into "+table+"("+s+") VALUES ("+t+")"
	elif(data["command"]=="delete"):
		s = ""
		sql = "delete from "+table 
		if("where" in data):
			sql = sql + " where "+ data["where"]
	#print(data)
	write_to_file(sql)
	connection = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
	channel = connection.channel()
	channel.queue_declare(queue='writeQ', durable=True)
	channel.basic_publish(
	exchange='',
	routing_key='writeQ',
	body=sql,
	properties=pika.BasicProperties(
		delivery_mode=2,  # make message persistent
	))
	connection.close()
	return jsonify({}),200

#clear commands.txt file to optimize
@app.route('/api/v1/db/clear',methods=["POST"])
def clear_db():
	open('commands.txt', 'w').close()
	return jsonify({}),200



@app.route('/api/v1/db/read',methods=["POST"])
def read_db():
	increment()
	url = "http://0.0.0.0:80/api/v1/count"
	response = requests.get(url=url)
	res = int(response.text)
	global g
	if(res==1 and g==0):
		g=1
		scheduler.start()
	data = request.get_json()
	#print(data)
	sql = "select "
	table=data["table"]
	if(data["command"]=="select"):
		if("column_list" in data):
			for i in data["column_list"]:
				sql = sql + i + ","
			sql = sql[:len(sql)-1] + " from " + table
		else:
			sql = sql+"* from "+table
		if("where" in data):
			sql = sql + " where "+ data["where"]
		if("group by" in data):
			sql = sql + " group by "
			for i in data["group by"]:
				sql = sql + i + ","
			sql = sql[:len(sql)-1]

	read_rpc = ReadDBClient()
	response = read_rpc.call(sql)
	
	return jsonify(response),200


@app.route('/api/v1/worker/list',methods=["GET"])
def list_worker():
	children = zk.get_children('/workers')
	y = []
	for c in children:
		d,s = zk.get("/workers/"+c)
		d = d.decode('utf-8')
		cid = d.split(";")[0]
		pid = d.split(";")[1]
		y.append(int(pid))
	return jsonify(sorted(y)), 200


@app.route('/api/v1/crash/slave',methods=["POST"])
def crash_slave():
	max_pid = 0
	max_cid = ""
	children = zk.get_children('/workers')
	for c in children:
		d,s = zk.get("/workers/"+c)
		d = d.decode('utf-8')
		cid = d.split(";")[0]
		pid = d.split(";")[1]
		role = d.split(";")[2]
		if((int(pid) > max_pid) and role=="slave"):
			max_pid = int(pid)
			max_cid = cid

	kill_slave(max_cid)
	#create_slave()
	l=[]
	l.append(str(max_pid))
	return jsonify(l), 200


@app.route('/api/v1/crash/master',methods=["POST"])
def crash_master():
	children = zk.get_children('/workers')
	mcid = ""
	mpic = 0
	for c in children:
		d,s = zk.get("/workers/"+c)
		d = d.decode('utf-8')
		cid = d.split(";")[0]
		pid = d.split(";")[1]
		role = d.split(";")[2]
		if(role=="master"):
			mcid = cid
			mpid = pid
			break
	kill_slave(mcid)
	l=[]
	l.append(str(mpid))
	return jsonify(l), 200



if __name__ == '__main__':	
	app.debug=True
	open('commands.txt', 'w').close() #clearing commands.txt file
	create_slave()
	scheduler = BackgroundScheduler() #scheduler to run every 2 minutes
	job = scheduler.add_job(check_func, 'interval', minutes=2) #adding job to scheduler
	app.run(host="0.0.0.0",port=80,use_reloader=False)
# -*- coding: utf-8 -*-
from proxy import LoggedProxy
from proxy.models import *
from config import config
from scanner.task import scan, sqlmap_scan
from scanner.fingerprint import gen_fingerprint
from lib.Log import *
from scanner.sqlmap import *
from collections import Iterable
import json
import Queue
import threading
import ctypes
import inspect


scan_tasks=Queue.Queue(config['tasks'])
terminate_mark=False

def _async_raise(tid, exctype):
    """raises the exception, performs cleanup if needed"""
    tid = ctypes.c_long(tid)
    if not inspect.isclass(exctype):
        exctype = type(exctype)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, ctypes.py_object(exctype))
    if res == 0:
        raise ValueError("invalid thread id")
    elif res != 1:
        # """if it returns a number greater than one, you're in trouble,
        # and you should call it again with exc=NULL to revert the effect"""
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, None)
        raise SystemError("PyThreadState_SetAsyncExc failed")


def stop_thread(thread):
    _async_raise(thread.ident, SystemExit)




def proxy_service():
    LoggedProxy.test(HandlerClass=LoggedProxy.LogRequestHandler)

def task_service():
    while(True):
        if terminate_mark:
            break
        try:
            task_count=scan_tasks.qsize()
            session=DBSession()
            Logs=session.query(Log).filter(Log.sended==False).limit(config['tasks']-task_count).all()
            if isinstance(Logs,Iterable):
                for l in Logs:
                    request={
                        'id':l.id,
                        'method':l.method,
                        'url':l.url,
                        'requestline':l.requestline,
                        'headers':json.loads(l.headers),
                        'req_body':l.req_body,
                        'time':l.time,
                        'sended':l.sended,
                        'dealed':l.dealed
                    }
                    logging.info("%s,%s"%(request['method'],request['url']))
                    for poc in config['pocs']:
                        scan_tasks.put(scan.delay(request,poc))
                    if(config['enable_sqlmap']):
                        scan_tasks.put(sqlmap_scan.delay(request))
                    l.sended=True
            session.commit()
        except Exception,e:
            logging.error(e.message)

def result_service():
    session=DBSession()
    while(True):
        if terminate_mark:
            break
        try:
            job=scan_tasks.get()
            if job.ready():
                result=job.get()
                logging.debug(result)
                if(not result['exists'] and result['found']>0):
                    res=Result()
                    res.fingerprint=gen_fingerprint(result['request'],result['type'])
                    res.type=result['type']
                    res.requestId=result['request']['id']
                    res.result=json.dumps(result['result'])
                    session.add(res)
                    session.commit()
                    session.query(Log).filter(Log.id==result['request']['id']).update({'dealed':True})
            else:
                scan_tasks.put(job)
            session.commit()
        except Exception,e:
            logging.error(e.message)


if __name__=='__main__':
    task_thread=threading.Thread(target=task_service)
    result_thread=threading.Thread(target=result_service)
    proxy_thread=threading.Thread(target=proxy_service)
    task_thread.start()

    result_thread.start()
    proxy_thread.start()
    threads=[task_thread,result_thread,proxy_thread]
    try:
        while True:
            for thread in threads:
                if not thread.isAlive():
                    logging.error("thread %s terminated,trying to recall it"%(thread.getName()))
                    logging.info("recall thread %s succeed"%(thread.getName()))
    except KeyboardInterrupt,e:
        logging.error('user termiated')
        for thread in threads:
            stop_thread(thread)


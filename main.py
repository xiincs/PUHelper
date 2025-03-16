from flask import Flask, jsonify, request
import requests
import json
import os
import time
import threading
from datetime import datetime
from flask_cors import CORS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'), template_folder=os.path.join(BASE_DIR, 'templates'))
CORS(app)

@app.route('/')
def index():
    return app.send_static_file('index.html')

# 预约列表存储路径
PRE_JOIN_FILE = 'pre_join_activities.json'

# 个人标识
AUTHORIZATION = None

# 加载预约列表
def load_pre_join_activities():
    if os.path.exists(PRE_JOIN_FILE):
        with open(PRE_JOIN_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

# 保存预约列表
def save_pre_join_activities(activities):
    with open(PRE_JOIN_FILE, 'w', encoding='utf-8') as f:
        json.dump(activities, f, ensure_ascii=False, indent=2)

# 全局预约列表
pre_join_activities = load_pre_join_activities()

# 定时任务线程
auto_join_threads = {}

# 检查并启动所有预约的定时任务
def check_and_start_auto_join_tasks():
    for activity in pre_join_activities:
        start_auto_join_task(activity)


def clean_expired_activities():
    global pre_join_activities
    now = time.time()
    original_count = len(pre_join_activities)
    
    # 过滤出未过期的活动
    valid_activities = []
    for activity in pre_join_activities:
        join_time = datetime.fromisoformat(activity['joinStartTime'].replace('Z', '+00:00')).timestamp()
        if join_time > now:
            valid_activities.append(activity)
        else:
            # 取消对应的定时任务
            if activity['id'] in auto_join_threads:
                auto_join_threads[activity['id']].cancel()
                del auto_join_threads[activity['id']]
    
    # 更新全局列表并保存
    if len(valid_activities) != original_count:
        pre_join_activities = valid_activities
        save_pre_join_activities(pre_join_activities)
        print(f"已清理{original_count - len(valid_activities)}个过期活动")


def schedule_cleanup_tasks():
    # 每小时执行一次清理
    clean_expired_activities()
    threading.Timer(3600, schedule_cleanup_tasks).start()

# 启动单个定时任务
def start_auto_join_task(activity):
    # 如果已经有该活动的定时任务，先取消
    if activity['id'] in auto_join_threads and auto_join_threads[activity['id']].is_alive():
        return
    
    # 计算延迟时间
    join_time = datetime.fromisoformat(activity['joinStartTime'].replace('Z', '+00:00')).timestamp()
    now = time.time()
    delay = max(0, join_time - now - 1)  # 提前1秒准备
    
    # 创建并启动线程，无论是否延迟都执行
    thread = threading.Timer(delay, auto_join, args=[activity])
    thread.daemon = True
    thread.start()
    auto_join_threads[activity['id']] = thread
    print(f"已设置活动 {activity['name']} 的自动抢课任务，将在 {datetime.fromtimestamp(join_time)} 执行")
    
    # 如果已经过了预定时间，立即执行抢课
    if delay == 0:
        print(f"当前时间已过预定时间，立即执行抢课任务：{activity['name']}")

# 自动抢课函数
def auto_join(activity):
    activity_id = activity['id']
    print(f"开始自动抢课：{activity['name']}")
    
    try:
        # 直接尝试抢课
        join_url = "https://apis.pocketuni.net/apis/activity/join"
        headers = {
            'Authorization': AUTHORIZATION,
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36',
            'Origin': 'https://class.pocketuni.net',
            'Referer': 'https://class.pocketuni.net/'
        }
        
        # 尝试多次请求，最多15次
        success = False
        attempts = 0
        max_attempts = 15
        
        while not success and attempts < max_attempts:
            join_response = requests.post(join_url, headers=headers, json={"activityId": activity_id})
            result = join_response.json()
            attempts += 1
            
            if result and (result['code'] == 0 or result['code'] == 9405) and result['data']:
                success = True
                print(f"自动抢课成功：{activity['name']}")
                
                # 从预约列表中移除
                global pre_join_activities
                pre_join_activities = [item for item in pre_join_activities if item['id'] != activity_id]
                save_pre_join_activities(pre_join_activities)
                break
            
            # 等待100ms后再次尝试
            if not success and attempts < max_attempts:
                time.sleep(0.1)
        
        if not success:
            print(f"自动抢课失败：{activity['name']}，已尝试{attempts}次")
            # 如果抢课失败且还在有效时间内，设置一个新的定时任务继续尝试
            now = time.time()
            join_time = datetime.fromisoformat(activity['joinStartTime'].replace('Z', '+00:00')).timestamp()
            if now < join_time:
                thread = threading.Timer(5, auto_join, args=[activity])
                thread.daemon = True
                thread.start()
                auto_join_threads[activity_id] = thread
                print(f"将在5秒后重新尝试抢课：{activity['name']}")
    
    except Exception as e:
        print(f"自动抢课出错：{str(e)}")
        # 如果发生错误且在有效时间内，设置一个新的定时任务重试
        now = time.time()
        join_time = datetime.fromisoformat(activity['joinStartTime'].replace('Z', '+00:00')).timestamp()
        if now < join_time:
            thread = threading.Timer(5, auto_join, args=[activity])
            thread.daemon = True
            thread.start()
            auto_join_threads[activity_id] = thread
            print(f"发生错误，将在5秒后重试：{activity['name']}")
    
    # 清理线程记录
    if activity_id in auto_join_threads:
        del auto_join_threads[activity_id]



@app.route('/activity/info', methods=['POST'])
def activity_info():
    url = "https://apis.pocketuni.net/apis/activity/info"
    
    headers = {
        'Authorization': AUTHORIZATION,  # 使用统一管理的 Authorization
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36',
        'Origin': 'https://class.pocketuni.net',
        'Referer': 'https://class.pocketuni.net/'
    }
    
    data = request.get_json()
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'code': -1, 'msg': f'请求失败，状态码：{response.status_code}'})
    except Exception as e:
        return jsonify({'code': -1, 'msg': f'发生错误：{str(e)}'})


@app.route('/activities', methods=['POST'])
def activities():
    url = "https://apis.pocketuni.net/apis/activity/list"
    
    headers = {
        'Authorization': AUTHORIZATION,  # 使用统一管理的 Authorization
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36',
        'Origin': 'https://class.pocketuni.net',
        'Referer': 'https://class.pocketuni.net/'
    }
    
    data = request.get_json()
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'code': -1, 'msg': f'请求失败，状态码：{response.status_code}'})
    except Exception as e:
        return jsonify({'code': -1, 'msg': f'发生错误：{str(e)}'})

@app.route('/join', methods=['POST'])
def join_activity():
    url = "https://apis.pocketuni.net/apis/activity/join"
    
    headers = {
        'Authorization': AUTHORIZATION,  # 使用统一管理的 Authorization
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36',
        'Origin': 'https://class.pocketuni.net',
        'Referer': 'https://class.pocketuni.net/'
    }
    
    data = request.get_json()
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'code': -1, 'msg': f'请求失败，状态码：{response.status_code}'})
    except Exception as e:
        return jsonify({'code': -1, 'msg': f'发生错误：{str(e)}'})


@app.route('/activity/cancel', methods=['POST'])
def activity_cancel():
    url = "https://apis.pocketuni.net/apis/activity/cancel"
    
    headers = {
        'Authorization': AUTHORIZATION,  # 使用统一管理的 Authorization
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36',
        'Origin': 'https://class.pocketuni.net',
        'Referer': 'https://class.pocketuni.net/'
    }
    
    data = request.get_json()
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'code': -1, 'msg': f'请求失败，状态码：{response.status_code}'})
    except Exception as e:
        return jsonify({'code': -1, 'msg': f'发生错误：{str(e)}'})


@app.route('/pre_join/list', methods=['GET'])
def get_pre_join_list():
    return jsonify({
        'code': 0,
        'data': pre_join_activities
    })

@app.route('/joined/list', methods=['POST'])
def get_joined_list():
    url = "https://apis.pocketuni.net/apis/activity/myList"
    data = request.get_json()
    page = data.get('page', 1)
    
    headers = {
        'Authorization': AUTHORIZATION,
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36',
        'Origin': 'https://class.pocketuni.net',
        'Referer': 'https://class.pocketuni.net/'
    }
    
    try:
        response = requests.post(url, headers=headers, json={
            'page': page,
            'limit': 4,
            'type': 2
        })
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'code': -1, 'msg': f'请求失败，状态码：{response.status_code}'})
    except Exception as e:
        app.logger.error(f'获取已报名列表失败: {str(e)}')
        return jsonify({'code': -1, 'msg': '服务器内部错误'})


@app.route('/pre_join/add', methods=['POST'])
def add_pre_join_activity():
    data = request.get_json()
    activity = data.get('activity')
    
    if not activity or not activity.get('id') or not activity.get('name') or not activity.get('joinStartTime'):
        return jsonify({'code': -1, 'msg': '参数不完整'})
    
    # 检查是否已存在
    if any(item['id'] == activity['id'] for item in pre_join_activities):
        return jsonify({'code': 0, 'msg': '该活动已在预抢课列表中'})
    
    # 添加到预约列表
    pre_join_activities.append({
        'id': activity['id'],
        'name': activity['name'],
        'joinStartTime': activity['joinStartTime']
    })
    
    # 保存到文件
    save_pre_join_activities(pre_join_activities)
    
    # 设置定时任务
    start_auto_join_task(pre_join_activities[-1])
    
    return jsonify({'code': 0, 'msg': '已添加到预抢课列表'})

@app.route('/pre_join/remove', methods=['POST'])
def remove_pre_join_activity():
    data = request.get_json()
    activity_id = data.get('activityId')
    
    if not activity_id:
        return jsonify({'code': -1, 'msg': '参数不完整'})
    
    # 从预约列表中移除
    global pre_join_activities
    original_length = len(pre_join_activities)
    pre_join_activities = [item for item in pre_join_activities if str(item['id']) != str(activity_id)]
    
    # 如果有线程正在运行，取消它
    if activity_id in auto_join_threads and auto_join_threads[activity_id].is_alive():
        auto_join_threads[activity_id].cancel()
        del auto_join_threads[activity_id]
    
    # 保存到文件
    save_pre_join_activities(pre_join_activities)
    
    if len(pre_join_activities) < original_length:
        return jsonify({'code': 0, 'msg': '已从预抢课列表中移除'})
    else:
        return jsonify({'code': 0, 'msg': '该活动不在预抢课列表中'})

@app.route('/check_auth', methods=['GET'])
def check_auth():
    return jsonify({'code': 0 if AUTHORIZATION else -1})

@app.route('/login', methods=['POST'])
def handle_login():
    try:
        data = request.get_json()
        login_url = 'https://apis.pocketuni.net/uc/user/login'
        response = requests.post(login_url, json={
            'userName': data['username'],
            'password': data['password'],
            'sid': 0,
            'device': 'pc'
        })
        
        if response.json()['code'] == 0:
            token_data = f"{response.json()['data']['token']}:{response.json()['data']['sid']}"
            global AUTHORIZATION
            AUTHORIZATION = f'Bearer {token_data}'
            with open('AUTHORIZATION.txt', 'w', encoding='utf-8') as f:
                f.write(AUTHORIZATION)
            return jsonify({'code': 0, 'msg': '登录成功'})
        return jsonify({'code': -1, 'msg': '登录失败'})
    except Exception as e:
        return jsonify({'code': -1, 'msg': str(e)})

if __name__ == '__main__':
    # 启动时检查并启动所有预约的定时任务
    check_and_start_auto_join_tasks()
    # 启动定期清理任务
    schedule_cleanup_tasks()
    app.run(debug=False, host='0.0.0.0', port=9999)

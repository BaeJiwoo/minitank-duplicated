import socket
import threading
import pickle
import random
import math
import time

HOST = '0.0.0.0'
PORT = 8080      # TCP 포트
UDP_PORT = 9000  # UDP 포트
WIDTH, HEIGHT = 800, 600

# UDP 소켓 설정
udp_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_server.bind((HOST, UDP_PORT))

client_udp_addrs = {}
data_lock = threading.Lock()

# 게임 상태 데이터
game_state = {
    'players': {},
    'obstacles': [],
    'bullets': [],
    'explosions': [],
    'kill_logs': []
}

obs_counter = 0
BULLET_SPEED = 500

def spawn_obstacle():
    global obs_counter
    for _ in range(50):
        r = random.randint(20, 50)
        x = random.randint(50 + r, WIDTH - 50 - r)
        y = random.randint(50 + r, HEIGHT - 50 - r)
        
        collision = False
        for obs in game_state['obstacles']:
            if math.hypot(x - obs['x'], y - obs['y']) < r + obs['r'] + 10:
                collision = True; break
        
        if not collision:
            obs_id = obs_counter
            obs_counter += 1
            hp = int(r / 3) + 5
            reward_lv = min(3, max(1, int((r - 10) / 10)))
            return {'id': obs_id, 'x': x, 'y': y, 'r': r, 'hp': hp, 'max_hp': hp, 'reward_lv': reward_lv}
    return None

# 초기 장애물 생성
for _ in range(15):
    o = spawn_obstacle()
    if o: game_state['obstacles'].append(o)

def update_player_stats(p):
    p['max_hp'] = 10 + (int(p['lv']) * 5)

# --- 1. UDP 수신 스레드: 실시간 위치 동기화 전담 ---
def udp_receive_thread():
    while True:
        try:
            raw_data, addr = udp_server.recvfrom(65535)
            client_data = pickle.loads(raw_data)
            
            p_id = client_data.get('p_id')
            if p_id is None: continue
            
            with data_lock:
                client_udp_addrs[p_id] = addr # 응답을 보낼 주소 저장
                
                # 플레이어 위치 및 각도 업데이트
                if 'me' in client_data and p_id in game_state['players']:
                    me = client_data['me']
                    p = game_state['players'][p_id]
                    if not p['dead']:
                        p['x'] = me['x']
                        p['y'] = me['y']
                        p['ba'] = me['ba']
                        p['ta'] = me['ta']
                        p['name'] = me['name']
                        p['c'] = me['c']
                
                # 전체 게임 상태 응답 전송
                reply_data = {
                    'players': game_state['players'],
                    'obstacles': game_state['obstacles'],
                    'bullets': game_state['bullets'],
                    'explosions': game_state['explosions'],
                    'kill_logs': game_state['kill_logs']
                }
                serialized = pickle.dumps(reply_data)
                udp_server.sendto(serialized, addr)
                
        except Exception as e:
            print(f"UDP Receive Error: {e}")

# --- 2. 게임 로직 스레드: 물리 연산 전담 ---
def game_logic_thread():
    global game_state
    prev_time = time.time()

    while True:
        now = time.time()
        dt = now - prev_time
        prev_time = now

        with data_lock:
            # 총알 이동
            surviving_bullets = []
            for b in game_state['bullets']:
                rad = math.radians(b['angle'])
                b['x'] += math.cos(rad) * BULLET_SPEED * dt
                b['y'] -= math.sin(rad) * BULLET_SPEED * dt
                b['life'] -= dt
                if 0 <= b['x'] <= WIDTH and 0 <= b['y'] <= HEIGHT and b['life'] > 0:
                    surviving_bullets.append(b)
            game_state['bullets'] = surviving_bullets

            # 충돌 처리 (중략된 기존 로직 유지)
            bullets_to_remove = []
            for b in game_state['bullets']:
                hit = False
                attacker = game_state['players'].get(b['p_id'])
                
                # 장애물 충돌 판정
                for i, obs in enumerate(game_state['obstacles']):
                    if math.hypot(b['x'] - obs['x'], b['y'] - obs['y']) < b['radius'] + obs['r']:
                        dmg = 1 + int(attacker['lv'] * 0.2) if attacker else 1
                        obs['hp'] -= dmg
                        game_state['explosions'].append({'x': b['x'], 'y': b['y'], 'r': 10, 'type': 'hit', 'time': now})
                        hit = True
                        if obs['hp'] <= 0:
                            if attacker:
                                attacker['lv'] += obs['reward_lv']
                                update_player_stats(attacker)
                                attacker['hp'] = attacker['max_hp']
                            game_state['obstacles'].pop(i)
                            new_obs = spawn_obstacle()
                            if new_obs: game_state['obstacles'].append(new_obs)
                        break
                
                if hit:
                    bullets_to_remove.append(b)
                    continue

                # 플레이어 충돌 판정
                for pid, p in game_state['players'].items():
                    if p['dead'] or pid == b['p_id']: continue
                    scale = 1 + (p['lv'] * 0.1)
                    p_radius = (40 * min(scale, 3.0)) / 2
                    if math.hypot(b['x'] - p['x'], b['y'] - p['y']) < b['radius'] + p_radius:
                        dmg = 2 + int(attacker['lv'] * 0.5) if attacker else 2
                        p['hp'] -= dmg
                        game_state['explosions'].append({'x': b['x'], 'y': b['y'], 'r': 15, 'type': 'hit', 'time': now})
                        hit = True
                        if p['hp'] <= 0:
                            p['hp'] = 0; p['dead'] = True
                            if attacker:
                                xp_gain = max(1, int(p['lv'] * 0.5))
                                attacker['lv'] += xp_gain
                                update_player_stats(attacker)
                                attacker['hp'] = attacker['max_hp']
                                game_state['kill_logs'].append({'msg': f"{attacker['name']}님이 {p['name']}님을 처치!", 'time': now + 4})
                        break
                if hit: bullets_to_remove.append(b)

            for b in bullets_to_remove:
                if b in game_state['bullets']: game_state['bullets'].remove(b)

            game_state['explosions'] = [e for e in game_state['explosions'] if now - e['time'] < 0.5]
            game_state['kill_logs'] = [k for k in game_state['kill_logs'] if k['time'] > now]

        time.sleep(0.016)

# --- 3. TCP 핸들러: 접속 및 중요 이벤트 전담 ---
def handle_client_tcp(conn, p_id):
    print(f"[TCP] 플레이어 {p_id} 입장")
    try:
        # 가짜 HTTP 핸드셰이크
        conn.recv(1024)
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        
        # 고유 ID 부여
        conn.send(pickle.dumps(p_id))

        with data_lock:
            game_state['players'][p_id] = {
                'x': -1000, 'y': -1000, 'name': 'Player', 
                'hp': 10, 'max_hp': 10, 'lv': 1.0, 
                'dead': False, 'ba': 0, 'ta': 0, 'c': (100,100,100)
            }

        while True:
            header = conn.recv(4)
            if not header: break
            size = int.from_bytes(header, 'big')
            
            # 대용량 데이터 대응 수신
            chunks = []
            recvd = 0
            while recvd < size:
                chunk = conn.recv(min(size - recvd, 4096))
                if not chunk: break
                chunks.append(chunk)
                recvd += len(chunk)
            
            event_data = pickle.loads(b''.join(chunks))

            with data_lock:
                p = game_state['players'].get(p_id)
                if not p: break

                # 중요 이벤트 처리 (리스폰, 총알 생성)
                if event_data.get('respawn_req'):
                    p.update({'hp': 10, 'max_hp': 10, 'lv': 1.0, 'dead': False})
                    p['x'], p['y'] = random.randint(100, 700), random.randint(100, 500)

                if 'new_bullets' in event_data and not p['dead']:
                    for b in event_data['new_bullets']:
                        b.update({'p_id': p_id, 'life': 1.5 + (p['lv']*0.1), 'radius': 4 + (p['lv']*0.5)})
                        game_state['bullets'].append(b)
    except:
        pass
    finally:
        with data_lock:
            if p_id in game_state['players']: del game_state['players'][p_id]
            if p_id in client_udp_addrs: del client_udp_addrs[p_id]
        conn.close()
        print(f"[TCP] 플레이어 {p_id} 퇴장")

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((HOST, PORT))
    except Exception as e:
        print(f"Bind Error: {e}")
        return
    server.listen()
    print(f"서버 가동 중... (TCP:{PORT}, UDP:{UDP_PORT})")
    
    threading.Thread(target=game_logic_thread, daemon=True).start()
    threading.Thread(target=udp_receive_thread, daemon=True).start()

    cid = 0
    while True:
        conn, addr = server.accept()
        # handle_client -> handle_client_tcp 로 수정됨
        threading.Thread(target=handle_client_tcp, args=(conn, cid), daemon=True).start()
        cid += 1

if __name__ == "__main__":
    main()
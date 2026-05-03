import socket
import threading
import json
import time
import uuid
import base64
import re
import os
from collections import defaultdict

# Game constants
GAME_WIDTH    = 2400
GAME_HEIGHT   = 1200
GRAVITY       = 0.6
JUMP_STRENGTH = -12
MOVE_SPEED    = 5
GROUND_LEVEL  = 1000

# Platforms: (x, y, width, height, color_r, color_g, color_b)
PLATFORMS = [
    # Main ground
    (0, GROUND_LEVEL, GAME_WIDTH, GAME_HEIGHT - GROUND_LEVEL, 139, 69, 19),

    # Left side – Beginner area
    (100, 900, 250, 20, 100, 200, 100),
    (400, 800, 200, 20, 100, 200, 100),
    (650, 700, 200, 20, 100, 200, 100),

    # Centre – Intermediate
    (1000, 850, 300, 20, 200, 150, 50),
    (1350, 700, 250, 20, 200, 150, 50),
    (1650, 550, 300, 20, 200, 150, 50),

    # Right side – Advanced
    (2000, 800, 200, 20, 200, 50, 50),
    (2200, 650, 250, 20, 200, 50, 50),
    (2050, 450, 300, 20, 200, 50, 50),

    # Upper – Challenge
    (400,  500, 150, 20, 150, 100, 200),
    (750,  400, 150, 20, 150, 100, 200),
    (1100, 350, 150, 20, 150, 100, 200),
    (1450, 400, 150, 20, 150, 100, 200),
    (1800, 500, 150, 20, 150, 100, 200),
]

# Admin credentials — read from environment variables for security
# Set these in Railway/cloud dashboard, fallback to defaults for local use
ADMIN_NAME     = os.environ.get("ADMIN_NAME",     "ADMIN")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "3295213")


class Player:
    def __init__(self, player_id, username="Player"):
        self.id         = player_id
        self.username   = f"{username}_{player_id}"
        self.x          = 50 + (player_id % 10) * 100
        self.y          = GROUND_LEVEL - 40
        self.width      = 30
        self.height     = 40
        self.vel_x      = 0
        self.vel_y      = 0
        self.on_ground  = False
        self.score      = 0
        self.jumps      = 0
        self.deaths     = 0
        self.is_admin   = False
        self.flying     = False
        self.invincible = False
        self.muted      = False      # NEW: admin can mute players
        self.spawn_time = time.time()

        colors = [
            [255, 0,   0],   [0,   0,   255], [0,   255, 0],
            [255, 255, 0],   [255, 0,   255], [0,   255, 255],
            [255, 165, 0],   [128, 0,   128], [255, 192, 203],
            [0,   128, 128],
        ]
        self.color = colors[player_id % len(colors)]

    def update(self, keys):
        move_speed    = MOVE_SPEED    * 2   if self.is_admin else MOVE_SPEED
        jump_strength = JUMP_STRENGTH * 1.5 if self.is_admin else JUMP_STRENGTH

        if keys.get('left'):
            self.vel_x = -move_speed
        elif keys.get('right'):
            self.vel_x =  move_speed
        else:
            self.vel_x = 0

        if self.flying:
            if keys.get('up'):
                self.vel_y = -move_speed
            elif keys.get('down'):
                self.vel_y =  move_speed
            else:
                self.vel_y = 0
        else:
            self.vel_y += GRAVITY
            if self.vel_y > 15:
                self.vel_y = 15

        self.x += self.vel_x
        self.y += self.vel_y

        # Boundary
        if self.x < 0:
            self.x = 0
        if self.x + self.width > GAME_WIDTH:
            self.x = GAME_WIDTH - self.width

        # Platform collision
        if not self.flying:
            self.on_ground = False
            for px, py, pw, ph, *_ in PLATFORMS:
                if (self.x + self.width > px and self.x < px + pw and
                        self.y + self.height >= py and
                        self.y + self.height <= py + ph + self.vel_y and
                        self.vel_y >= 0):
                    self.y        = py - self.height
                    self.vel_y    = 0
                    self.on_ground= True
                    break
        else:
            self.on_ground = False

        # Death
        if self.y > GAME_HEIGHT and not self.is_admin and not self.invincible:
            self.deaths += 1
            self.respawn()

    def respawn(self):
        self.x     = 50 + (self.id % 10) * 100
        self.y     = GROUND_LEVEL - 40
        self.vel_y = 0

    def jump(self):
        if self.on_ground:
            js       = JUMP_STRENGTH * 1.5 if self.is_admin else JUMP_STRENGTH
            self.vel_y = js
            self.jumps += 1
            self.score += 1

    def toggle_flight(self):
        if self.is_admin:
            self.flying = not self.flying

    def to_dict(self):
        return {
            'id'       : self.id,
            'username' : self.username,
            'x'        : self.x,
            'y'        : self.y,
            'width'    : self.width,
            'height'   : self.height,
            'color'    : self.color,
            'vel_x'    : self.vel_x,
            'vel_y'    : self.vel_y,
            'score'    : self.score,
            'jumps'    : self.jumps,
            'deaths'   : self.deaths,
            'is_admin' : self.is_admin,
            'flying'   : self.flying,
            'muted'    : self.muted,
            'uptime'   : round(time.time() - self.spawn_time),
        }


class GameServer:
    def __init__(self, host='0.0.0.0', port=5555):
        self.host   = host
        self.port   = port
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(64)

        self.clients       = {}
        self.players       = {}
        self.player_keys   = defaultdict(lambda: {'left':False,'right':False,'jump':False})
        self.chat_messages = []
        self.next_player_id= 0
        self.running       = True
        self.lock          = threading.Lock()

        # Server-side log (for console reflection)
        self.server_log    = []

        print(f"[SERVER] ILA Platformer started on {self.host}:{self.port}")
        print(f"[SERVER] World: {GAME_WIDTH}×{GAME_HEIGHT}  |  Platforms: {len(PLATFORMS)}")
        print("[SERVER] Waiting for players…")

    # ──────────────────────────────────────────────────────────────────────────
    #  Send helpers
    # ──────────────────────────────────────────────────────────────────────────
    def _send(self, conn, obj):
        """Encode and send a JSON message with 4-byte length prefix."""
        try:
            data = json.dumps(obj).encode()
            conn.sendall(len(data).to_bytes(4,'big') + data)
        except Exception:
            pass

    def _broadcast(self, obj, exclude=None):
        """Broadcast to all connected clients."""
        data = json.dumps(obj).encode()
        msg  = len(data).to_bytes(4,'big') + data
        for pid, conn in list(self.clients.items()):
            if pid == exclude:
                continue
            try:
                conn.sendall(msg)
            except Exception:
                pass

    def _send_to(self, pid, obj):
        if pid in self.clients:
            self._send(self.clients[pid], obj)

    def _sys_chat(self, text):
        """Broadcast a system-level chat message (gold, SERVER tag)."""
        chat = {
            'player_id': -1,
            'username' : 'SERVER',
            'message'  : text,
            'is_admin' : True,
            'timestamp': time.time(),
        }
        with self.lock:
            self.chat_messages.append(chat)
        self._broadcast({'type':'chat','data': chat})

    # ──────────────────────────────────────────────────────────────────────────
    #  Client handler
    # ──────────────────────────────────────────────────────────────────────────
    def handle_client(self, conn, addr):
        player_id = None
        try:
            with self.lock:
                player_id = self.next_player_id
                self.next_player_id += 1
                self.clients[player_id] = conn
                self.players[player_id] = Player(player_id)

            print(f"[CONNECT] Player {player_id} from {addr}")

            # Init
            self._send(conn, {'type':'init','player_id': player_id})

            # Send chat history
            for chat in self.chat_messages:
                self._send(conn, {'type':'chat','data': chat})

            # Welcome system message
            self._sys_chat(f"Player {player_id} joined the game!")

            buffer = b''
            while self.running:
                try:
                    conn.settimeout(30)
                    data = conn.recv(4096)
                    if not data:
                        break
                    buffer += data

                    while len(buffer) >= 4:
                        msg_len = int.from_bytes(buffer[:4],'big')
                        if len(buffer) < 4 + msg_len:
                            break
                        msg_data = buffer[4:4+msg_len]
                        buffer   = buffer[4+msg_len:]
                        msg      = json.loads(msg_data.decode())
                        self._handle_message(player_id, msg)

                except socket.timeout:
                    continue
                except Exception:
                    break

        except Exception as e:
            print(f"[ERROR] Player {player_id}: {e}")
        finally:
            if player_id is not None:
                with self.lock:
                    self.clients.pop(player_id, None)
                    self.players.pop(player_id, None)
                    self.player_keys.pop(player_id, None)
                try:
                    conn.close()
                except Exception:
                    pass
                print(f"[DISCONNECT] Player {player_id}. Online: {len(self.clients)}")
                self._sys_chat(f"Player {player_id} left the game.")

    def _handle_message(self, player_id, msg):
        t = msg.get('type')

        if t == 'input':
            self.player_keys[player_id] = msg.get('keys', {})

        elif t == 'set_name':
            new_name = msg.get('name', '').strip()[:20]
            if new_name:
                with self.lock:
                    if player_id in self.players:
                        safe = ''.join(c for c in new_name if c.isprintable())
                        self.players[player_id].username = f"{safe}_{player_id}"
                        print(f"[SERVER] Player {player_id} set name: {safe}")

        elif t == 'jump':
            with self.lock:
                if player_id in self.players:
                    self.players[player_id].jump()

        elif t == 'flight':
            with self.lock:
                if player_id in self.players:
                    self.players[player_id].toggle_flight()

        elif t == 'auth':
            if msg.get('name') == ADMIN_NAME and msg.get('password') == ADMIN_PASSWORD:
                with self.lock:
                    if player_id in self.players:
                        self.players[player_id].is_admin = True
                        print(f"[ADMIN] Player {player_id} authenticated as admin")
                self._broadcast({'type':'admin_auth','player_id': player_id,'status': True})
                self._sys_chat(f"★ Player {player_id} is now an Admin!")

        elif t == 'chat':
            with self.lock:
                p = self.players.get(player_id)
                if not p or p.muted:
                    if p and p.muted:
                        self._send_to(player_id, {
                            'type':'chat','data':{
                                'player_id':-1,'username':'SERVER',
                                'message':'You are muted.','is_admin':True,
                                'timestamp': time.time()
                            }
                        })
                    return
                is_admin = p.is_admin
                username = p.username
            chat_data = {
                'player_id': player_id,
                'username' : username,
                'message'  : msg.get('message','')[:200],
                'is_admin' : is_admin,
                'timestamp': time.time(),
            }
            with self.lock:
                self.chat_messages.append(chat_data)
                if len(self.chat_messages) > 200:
                    self.chat_messages.pop(0)
            self._broadcast({'type':'chat','data': chat_data})

        # ── Admin-only commands ──────────────────────────────────────────────
        elif t == 'admin_kick':
            with self.lock:
                req = self.players.get(player_id)
                if not req or not req.is_admin:
                    return
            target_id = msg.get('target_id')
            if target_id in self.clients:
                try:
                    self._send_to(target_id, {'type':'kicked','reason':'Kicked by admin'})
                    self.clients[target_id].close()
                except Exception:
                    pass
                print(f"[ADMIN] Player {player_id} kicked Player {target_id}")
                self._send_to(player_id, {'type':'kick_result','result':f"OK: Kicked Player {target_id}"})
            else:
                self._send_to(player_id, {'type':'kick_result','result':f"ERR: Player {target_id} not found"})

        elif t == 'admin_teleport':
            with self.lock:
                req = self.players.get(player_id)
                target = self.players.get(msg.get('target_id'))
                if req and req.is_admin and target:
                    req.x = target.x + 40
                    req.y = target.y

        elif t == 'admin_reset_score':
            with self.lock:
                req = self.players.get(player_id)
                target = self.players.get(msg.get('target_id'))
                if req and req.is_admin and target:
                    target.score  = 0
                    target.jumps  = 0
                    target.deaths = 0
                    print(f"[ADMIN] Player {player_id} reset score of Player {target.id}")

        elif t == 'admin_mute':
            with self.lock:
                req = self.players.get(player_id)
                target = self.players.get(msg.get('target_id'))
                if req and req.is_admin and target:
                    target.muted = not target.muted
                    status = "muted" if target.muted else "unmuted"
                    print(f"[ADMIN] {target.username} is now {status}")
                    self._sys_chat(f"Player {target.id} has been {status}.")

        elif t == 'admin_respawn':
            with self.lock:
                req = self.players.get(player_id)
                target = self.players.get(msg.get('target_id'))
                if req and req.is_admin and target:
                    target.respawn()

    # ──────────────────────────────────────────────────────────────────────────
    #  Game loop
    # ──────────────────────────────────────────────────────────────────────────
    def broadcast_state(self):
        state = {
            'type'      : 'state',
            'players'   : [p.to_dict() for p in self.players.values()],
            'platforms' : PLATFORMS,
            'game_width': GAME_WIDTH,
            'game_height': GAME_HEIGHT,
            'timestamp' : time.time(),
        }
        data = json.dumps(state).encode()
        msg  = len(data).to_bytes(4,'big') + data
        for conn in list(self.clients.values()):
            try:
                conn.sendall(msg)
            except Exception:
                pass

    def game_loop(self):
        last_print = time.time()
        frames     = 0
        while self.running:
            with self.lock:
                for pid, player in list(self.players.items()):
                    player.update(self.player_keys[pid])
            self.broadcast_state()
            frames += 1
            if time.time() - last_print >= 5:
                print(f"[SERVER] Players: {len(self.clients)} | FPS: {frames//5}")
                frames     = 0
                last_print = time.time()
            time.sleep(1/60)

    def start(self):
        threading.Thread(target=self.game_loop, daemon=True).start()
        try:
            while self.running:
                try:
                    self.server.settimeout(1)
                    conn, addr = self.server.accept()
                    threading.Thread(target=self.handle_client,
                                     args=(conn, addr), daemon=True).start()
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            print("\n[SERVER] Shutting down…")
        finally:
            self.stop()

    def stop(self):
        self.running = False
        with self.lock:
            for conn in self.clients.values():
                try: conn.close()
                except: pass
        try: self.server.close()
        except: pass
        print("[SERVER] Shutdown complete.")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5555))
    server = GameServer('0.0.0.0', port)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()

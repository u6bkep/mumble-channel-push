import Ice, IcePy
import MumbleServer
from flask import Flask, jsonify, Response, send_from_directory
import threading
import signal
import sys
import json
import time
import argparse
import os
from collections import defaultdict

app = Flask(__name__)

# Global variables to keep references
ice = None
adapters = []
trackers = {}  # Map server_id to tracker
sse_clients = defaultdict(list)  # Map server_id to list of clients

# Parse command line arguments
def parse_args():
    parser = argparse.ArgumentParser(description='Mumble Channel Push - Monitor and expose channel state via HTTP')
    parser.add_argument('--host', default=os.environ.get('MUMBLE_ICE_HOST', 'localhost'), 
                        help='Ice host (default: from MUMBLE_ICE_HOST env var or localhost)')
    parser.add_argument('--port', type=int, default=int(os.environ.get('MUMBLE_ICE_PORT', '6502')), 
                        help='Ice port (default: from MUMBLE_ICE_PORT env var or 6502)')
    parser.add_argument('--secret', default=os.environ.get('MUMBLE_ICE_SECRET', ''), 
                        help='Ice secret (default: from MUMBLE_ICE_SECRET env var)')
    parser.add_argument('--web-host', default=os.environ.get('CVP_HTTP_HOST', '0.0.0.0'), 
                        help='Web server host (default: from CVP_HTTP_HOST env var or 0.0.0.0)')
    parser.add_argument('--web-port', type=int, default=int(os.environ.get('CVP_HTTP_PORT', '5000')), 
                        help='Web server port (default: from CVP_HTTP_PORT env var or 5000)')
    parser.add_argument('--ice-host', default=os.environ.get('CVP_ICE_HOST', 'localhost'),
                        help='Ice callback host (default: from CVP_ICE_HOST env var or localhost)')
    return parser.parse_args()

class MumbleChannelTrackerI(MumbleServer.ServerCallback):
    """
    Tracks channel and user state in a Mumble server and receives callbacks when changes occur.
    Implements the MumbleServer.ServerCallback interface.
    """
    def __init__(self, server):
        self.server = server
        self.server_id = server.id()
        self.channels = {}  # Map of channel IDs to Channel objects
        self.users = {}     # Map of session IDs to User objects
        self.lock = threading.RLock()  # Thread-safe updates
        self.refresh()
        
    def refresh(self):
        """Refresh all channels and users from the server"""
        with self.lock:
            try:
                self.channels = self.server.getChannels()
                self.users = self.server.getUsers()
            except Exception as e:
                print(f"Error refreshing data: {e}")
    
    def get_channel_tree(self):
        """Build a tree structure of channels"""
        with self.lock:
            # Create a map of parent to children
            parent_map = {}
            for channel_id, channel in self.channels.items():
                parent = channel.parent
                if parent not in parent_map:
                    parent_map[parent] = []
                parent_map[parent].append(channel_id)
            
            # Build tree starting from root (0)
            return {
                'name': self.server.getConf('registername') or f"Server {self.server_id}",
                'root': self._build_channel_tree(0, parent_map)
            }
            
    def _build_channel_tree(self, channel_id, parent_map):
        """Recursive helper to build channel tree"""
        channel_info = {
            'id': channel_id,
            'name': self.channels[channel_id].name if channel_id in self.channels else "Root",
            'channels': [],
            'users': self.get_users_in_channel(channel_id)
        }
        
        if channel_id in parent_map:
            for child_id in sorted(parent_map[channel_id]):
                channel_info['channels'].append(self._build_channel_tree(child_id, parent_map))
                
        return channel_info
            
    def get_users_in_channel(self, channel_id):
        """Return list of users in a channel"""
        with self.lock:
            result = []
            for session, user in self.users.items():
                if user.channel == channel_id:
                    result.append({
                        'session': user.session,
                        'name': user.name,
                        'mute': user.mute,
                        'deaf': user.deaf,
                        'selfMute': user.selfMute,
                        'selfDeaf': user.selfDeaf,
                        'online': user.onlinesecs
                    })
            return result

    def _notify_clients(self):
        """Notify all SSE clients for this server about changes"""
        if self.server_id in sse_clients and sse_clients[self.server_id]:
            tree = self.get_channel_tree()
            data = f"data: {json.dumps(tree)}\n\n"
            # Notify all clients for this server
            for client in sse_clients[self.server_id]:
                client.put(data)

    # ServerCallback interface methods
    def userConnected(self, state, current=None):
        print(f"User connected: {state.name} on server {self.server_id}")
        with self.lock:
            self.users[state.session] = state
        self._notify_clients()
        
    def userDisconnected(self, state, current=None):
        print(f"User disconnected: {state.name} from server {self.server_id}")
        with self.lock:
            if state.session in self.users:
                del self.users[state.session]
        self._notify_clients()
            
    def userStateChanged(self, state, current=None):
        print(f"User state changed: {state.name} on server {self.server_id}")
        with self.lock:
            self.users[state.session] = state
        self._notify_clients()
        
    def userTextMessage(self, state, message, current=None):
        print(f"Text message from {state.name} on server {self.server_id}: {message.text}")
        
    def channelCreated(self, state, current=None):
        print(f"Channel created: {state.name} on server {self.server_id}")
        with self.lock:
            self.channels[state.id] = state
        self._notify_clients()
        
    def channelRemoved(self, state, current=None):
        print(f"Channel removed: {state.name} from server {self.server_id}")
        with self.lock:
            if state.id in self.channels:
                del self.channels[state.id]
        self._notify_clients()
            
    def channelStateChanged(self, state, current=None):
        print(f"Channel state changed: {state.name} on server {self.server_id}")
        with self.lock:
            self.channels[state.id] = state
        self._notify_clients()

def event_stream(server_id):
    """Generator for SSE events"""
    # Create a queue for this client
    queue = Queue()
    sse_clients[server_id].append(queue)
    try:
        # Send initial data
        if server_id in trackers:
            tree = trackers[server_id].get_channel_tree()
            yield f"data: {json.dumps(tree)}\n\n"
        
        # Wait for updates
        while True:
            data = queue.get()
            yield data
    finally:
        # Clean up when client disconnects
        sse_clients[server_id].remove(queue)

class Queue:
    """Simple queue for SSE messages"""
    def __init__(self):
        self.messages = []
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
    
    def put(self, message):
        with self.lock:
            self.messages.append(message)
            self.condition.notify()
    
    def get(self):
        with self.condition:
            while not self.messages:
                self.condition.wait()
            return self.messages.pop(0)

def initialize_mumble_connection(args):
    global ice, trackers, adapters
    
    try:
        prop = Ice.createProperties([])
        prop.setProperty("Ice.ImplicitContext", "Shared")
        prop.setProperty("Ice.MessageSizeMax", "65535")

        idd = Ice.InitializationData()
        idd.properties = prop
        ice = Ice.initialize(idd)
        ice.getImplicitContext().put("secret", args.secret)

        constring = f"Meta -e 1.0:tcp -h {args.host} -p {args.port}"

        prx = ice.stringToProxy(constring)

        prx.ice_ping()

        print("Ping successful")
        
        # Now we can cast the proxy to the Meta interface
        # meta = mumble_ice_module.MetaPrx.checkedCast(prx)
        meta = MumbleServer.MetaPrx.checkedCast(prx)

        murmurversion = meta.getVersion()[:3]
        print(f"Murmur version: {murmurversion}")

        try:
            booted = meta.getBootedServers()
        except MumbleServer.InvalidSecretException:
            print("Invalid secret provided. Please check your configuration.")
            return False

        print(f"booted servers: {len(booted)}")

        # Create the tracker and register it with the server
        if booted:
            for server in booted:
                server_id = server.id()
                
                # Create a unique adapter for each callback
                adapter_name = f"Callback.Client.{server_id}"
                adapter = ice.createObjectAdapterWithEndpoints(adapter_name, f"tcp -h {args.ice_host}")
                adapter.activate()
                adapters.append(adapter)  # Keep a reference to the adapter
                
                # Create and register the callback object
                tracker_servant = MumbleChannelTrackerI(server)
                callback_identity = Ice.stringToIdentity(f"callback-{server_id}")
                
                # Pass the correct module interface
                tracker_proxy = MumbleServer.ServerCallbackPrx.uncheckedCast(
                    adapter.add(tracker_servant, callback_identity)
                )
                
                server.addCallback(tracker_proxy)
                trackers[server_id] = tracker_servant  # Map server_id to tracker
                
                print(f"Channel tracker registered with server {server_id}")
            
            return True
        else:
            print("No booted servers found")
            return False
            
    except Exception as e:
        print(f"Error initializing Mumble connection: {e}")
        import traceback
        traceback.print_exc()
        if ice:
            try:
                ice.destroy()
            except:
                pass
        return False

def cleanup():
    global ice, adapters
    print("Cleaning up Ice resources...")
    
    # Deactivate all adapters first
    for adapter in adapters:
        try:
            adapter.deactivate()
        except:
            pass
    
    # Then destroy Ice
    if ice:
        try:
            ice.destroy()
        except:
            pass

def signal_handler(sig, frame):
    print('Shutting down...')
    cleanup()
    sys.exit(0)

# New API routes
@app.route('/servers')
def get_servers():
    """Return a list of all server IDs"""
    return jsonify({"servers": list(trackers.keys())})

@app.route('/<int:server_id>')
def get_server(server_id):
    """Return the channel tree for a specific server"""
    if server_id in trackers:
        return jsonify(trackers[server_id].get_channel_tree())
    return jsonify({"error": f"Server {server_id} not found"}), 404

@app.route('/listen/<int:server_id>')
def listen_server(server_id):
    """SSE endpoint to listen for server updates"""
    if server_id not in trackers:
        return jsonify({"error": f"Server {server_id} not found"}), 404
    
    return Response(
        event_stream(server_id),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }
    )

@app.route('/')
@app.route('/web')
def serve_web():
    """Serve the main HTML page"""
    return send_from_directory('static', 'server_viewer.html')

@app.route('/favicon.ico')
def serve_favicon():
    """Serve the favicon"""
    return send_from_directory('static', 'favicon.ico')

# server static icons from the static directory
@app.route('/icons/<path:filename>')
def serve_icon(filename):
    """Serve icons from the static directory"""
    return send_from_directory('static/icons', filename)

if __name__ == "__main__":
    # Parse command line arguments
    args = parse_args()

    print(f"Starting with host={args.host}, ice_port={args.port}")
    
    # Set up signal handler for clean shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Initialize Mumble connection
    if initialize_mumble_connection(args):
        # Run Flask in a separate thread to allow Ice callbacks to work
        flask_thread = threading.Thread(
            target=app.run, 
            kwargs={'debug': False, 'host': args.web_host, 'port': args.web_port}
        )
        flask_thread.daemon = True
        flask_thread.start()
        
        print(f"Flask server started in background on {args.web_host}:{args.web_port}")
        
        # Keep the main thread alive to receive Ice callbacks
        try:
            # This keeps the main thread alive and processing Ice events
            while True:
                signal.pause()
        except KeyboardInterrupt:
            pass
        finally:
            cleanup()
    else:
        print("Failed to initialize Mumble connection")
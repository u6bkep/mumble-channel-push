import Ice, IcePy
import MumbleServer
import signal
import sys
import time
import argparse
import traceback  # Added for better error reporting

# Class to handle callbacks from the Mumble server
class MumbleCallbackHandler(MumbleServer.ServerCallback):
    def __init__(self, server_id):
        self.server_id = server_id
        print(f"[DEBUG] Callback handler initialized for server {server_id}")
        
    def userConnected(self, state, current=None):
        print(f"[CALLBACK] User connected: {state.name} on server {self.server_id}")
        print(f"[DEBUG] Current context: {current}")
        
    def userDisconnected(self, state, current=None):
        print(f"[CALLBACK] User disconnected: {state.name} from server {self.server_id}")
        print(f"[DEBUG] Current context: {current}")
            
    def userStateChanged(self, state, current=None):
        print(f"[CALLBACK] User state changed: {state.name} on server {self.server_id}")
        print(f"[DEBUG] Current context: {current}")
        
    def userTextMessage(self, state, message, current=None):
        print(f"[CALLBACK] Text message from {state.name} on server {self.server_id}: {message.text}")
        print(f"[DEBUG] Current context: {current}")
        
    def channelCreated(self, state, current=None):
        print(f"[CALLBACK] Channel created: {state.name} on server {self.server_id}")
        print(f"[DEBUG] Current context: {current}")
        
    def channelRemoved(self, state, current=None):
        print(f"[CALLBACK] Channel removed: {state.name} from server {self.server_id}")
        print(f"[DEBUG] Current context: {current}")
            
    def channelStateChanged(self, state, current=None):
        print(f"[CALLBACK] Channel state changed: {state.name} on server {self.server_id}")
        print(f"[DEBUG] Current context: {current}")

# Initialize Ice and connect to Mumble server
def initialize_mumble_connection(host, port, secret):
    global ice, adapters
    
    ice = None
    adapters = []
    
    try:
        # Set up Ice properties
        prop = Ice.createProperties([])
        prop.setProperty("Ice.ImplicitContext", "Shared")
        prop.setProperty("Ice.MessageSizeMax", "65535")
        # Add debug properties
        prop.setProperty("Ice.Trace.Network", "2")
        prop.setProperty("Ice.Trace.Protocol", "1")

        idd = Ice.InitializationData()
        idd.properties = prop
        ice = Ice.initialize(idd)
        ice.getImplicitContext().put("secret", secret)  # Use the secret from arguments
        print(f"[DEBUG] Ice initialized with secret: {secret}")

        # Connect to the Mumble Meta service
        constring = f"Meta -e 1.0:tcp -h {host} -p {port}"
        print(f"[DEBUG] Connection string: {constring}")
        prx = ice.stringToProxy(constring)
        
        print("[DEBUG] Attempting ice ping...")
        prx.ice_ping()
        print("[DEBUG] Ping successful")

        # Get the Meta interface
        print("[DEBUG] Casting to Meta interface...")
        meta = MumbleServer.MetaPrx.checkedCast(prx)
        print("[DEBUG] Meta interface obtained")

        # Print Murmur version
        murmurversion = meta.getVersion()[:3]
        print(f"[DEBUG] Murmur version: {murmurversion}")

        # Get all running servers
        print("[DEBUG] Getting booted servers...")
        booted = meta.getBootedServers()
        print(f"[DEBUG] Booted servers: {len(booted)}")

        # Connect to each server and register callbacks
        for server in booted:
            server_id = server.id()
            print(f"[DEBUG] Processing server ID: {server_id}")
            
            # Create a unique adapter for this callback
            adapter_name = f"Callback.Client.{server_id}"
            print(f"[DEBUG] Creating adapter: {adapter_name}")
            adapter = ice.createObjectAdapterWithEndpoints(adapter_name, f"tcp -h {host}")
            adapter.activate()
            print(f"[DEBUG] Adapter activated: {adapter_name}")
            adapters.append(adapter)  # Keep a reference to the adapter
            
            # Create and register the callback object
            callback_handler = MumbleCallbackHandler(server_id)
            callback_identity = Ice.stringToIdentity(f"callback-{server_id}")
            print(f"[DEBUG] Creating callback with identity: {callback_identity}")
            callback_proxy = MumbleServer.ServerCallbackPrx.uncheckedCast(
                adapter.add(callback_handler, callback_identity)
            )
            
            print(f"[DEBUG] Adding callback to server {server_id}...")
            server.addCallback(callback_proxy)
            print(f"[DEBUG] Callback registered with server {server_id}")
            
            # Test if server is accessible
            print(f"[DEBUG] Testing server {server_id} - Getting uptime...")
            uptime = server.getUptime()
            print(f"[DEBUG] Server {server_id} uptime: {uptime} seconds")
            
            # Print current users on this server
            try:
                users = server.getUsers()
                print(f"[DEBUG] Server {server_id} has {len(users)} connected users")
                for user_id, user in users.items():
                    print(f"[DEBUG] User: {user.name} (ID: {user_id})")
            except Exception as e:
                print(f"[DEBUG] Error getting users: {e}")
        
        return True
            
    except Exception as e:
        print(f"[ERROR] Error initializing Mumble connection: {e}")
        print("[ERROR] Exception traceback:")
        traceback.print_exc()
        if ice:
            try:
                ice.destroy()
            except:
                pass
        return False

def cleanup():
    print("[DEBUG] Cleaning up Ice resources...")
    
    # Deactivate all adapters first
    for i, adapter in enumerate(adapters):
        try:
            print(f"[DEBUG] Deactivating adapter {i}")
            adapter.deactivate()
        except Exception as e:
            print(f"[ERROR] Error deactivating adapter {i}: {e}")
    
    # Then destroy Ice
    if ice:
        try:
            print("[DEBUG] Destroying Ice...")
            ice.destroy()
            print("[DEBUG] Ice destroyed successfully")
        except Exception as e:
            print(f"[ERROR] Error destroying Ice: {e}")

def signal_handler(sig, frame):
    print('[DEBUG] Signal received. Shutting down...')
    cleanup()
    sys.exit(0)

if __name__ == "__main__":
    # Global variables to keep references
    ice = None
    adapters = []
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='Mumble server callback client')
    parser.add_argument('--host', default='localhost', help='Mumble server host (default: localhost)')
    parser.add_argument('--port', type=int, default=6502, help='Mumble Ice port (default: 6502)')
    parser.add_argument('--secret', default='', help='Mumble Ice secret')
    args = parser.parse_args()
    
    print(f"[DEBUG] Starting with host={args.host}, port={args.port}, secret={args.secret}")
    
    # Set up signal handlers for clean shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Connect to Mumble
    if initialize_mumble_connection(args.host, args.port, args.secret):
        print(f"[DEBUG] Connected to Mumble server at {args.host}:{args.port}. Waiting for events (press Ctrl+C to exit)...")
        
        # Keep the script running to receive callbacks
        try:
            counter = 0
            while True:
                time.sleep(10)
                counter += 1
                print(f"[DEBUG] Still waiting for events... ({counter * 10}s)")
        except KeyboardInterrupt:
            print("[DEBUG] KeyboardInterrupt received")
        finally:
            cleanup()
    else:
        print(f"[ERROR] Failed to connect to Mumble server at {args.host}:{args.port}")

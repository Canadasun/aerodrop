"""PX4 SITL delivery executor controlled by the local prototype API."""
from __future__ import annotations
import json
import math
import threading
import time
import urllib.request

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from px4_msgs.msg import (BatteryStatus, OffboardControlMode, TrajectorySetpoint,
                          VehicleCommand, VehicleCommandAck, VehicleLocalPosition,
                          VehicleStatus, VehicleLandDetected)
from std_msgs.msg import String
from aerodrop_core.coordinates import wgs84_to_ned
from aerodrop_core.flight_logic import return_corridor, near_zone


def topic(name, cls):
    version = getattr(cls, 'MESSAGE_VERSION', 0)
    return '/fmu/out/' + name + (f'_v{version}' if version else '')


class MissionExecutor(Node):
    ACTIVE = {'ARMING', 'TAKEOFF', 'NAVIGATING', 'DELIVERY_HOVER', 'WINCH_DROP',
              'RETURNING', 'LANDING', 'RTL_FAILSAFE'}
    def __init__(self):
        super().__init__('aerodrop_mission_executor')
        for key, value in [('backend_url','http://127.0.0.1:8000/route'),
                           ('home_latitude',51.115), ('home_longitude',-114.110),
                           ('delivery_latitude',51.130), ('delivery_longitude',-114.089),
                           ('simulate_low_battery_after_seconds',-1.0)]:
            self.declare_parameter(key,value)
        self.base = str(self.get_parameter('backend_url').value).rsplit('/',1)[0]
        self.home_lat = float(self.get_parameter('home_latitude').value)
        self.home_lon = float(self.get_parameter('home_longitude').value)
        self.state = 'WAITING_FOR_POSITION'
        self.position = self.status = self.battery = self.land = None
        self.position_received = self.status_received = 0.0
        self.route = []; self.return_route = []; self.route_index = 0
        self.mission_id = None; self.last_command_id = None; self.pending = None
        self.last_action_id = None
        self.lock = threading.Lock(); self.action = None
        self.started = self.state_started = time.monotonic()
        self.last_request = 0.0; self.setpoints = 0; self.injection = False
        self.started_unix = None; self.ended_unix = None
        self.ended_at = None; self.payload_released = False; self.payload_skipped = False
        self.detail = ''; self.launch_x = self.launch_y = None
        self.battery_pct = None; self.low_battery_latched = False
        qos = QoSProfile(depth=10,reliability=ReliabilityPolicy.BEST_EFFORT)
        for cls,name,callback in [(VehicleLocalPosition,'vehicle_local_position',self.on_position),
                                  (VehicleStatus,'vehicle_status',self.on_status),
                                  (BatteryStatus,'battery_status',self.on_battery),
                                  (VehicleLandDetected,'vehicle_land_detected',self.on_land),
                                  (VehicleCommandAck,'vehicle_command_ack',self.on_ack)]:
            self.create_subscription(cls,topic(name,cls),callback,qos)
        self.state_pub = self.create_publisher(String,'/aerodrop/mission_state',10)
        self.mode_pub = self.create_publisher(OffboardControlMode,'/fmu/in/offboard_control_mode',10)
        self.sp_pub = self.create_publisher(TrajectorySetpoint,'/fmu/in/trajectory_setpoint',10)
        self.command_pub = self.create_publisher(VehicleCommand,'/fmu/in/vehicle_command',10)
        self.create_timer(0.1,self.tick)
        self.create_timer(0.5,self.publish_state)
        threading.Thread(target=self.poll,daemon=True).start()

    def on_position(self,msg):
        if msg.xy_valid and msg.z_valid:
            self.position=msg; self.position_received=time.monotonic()
    def on_status(self,msg):
        self.status=msg; self.status_received=time.monotonic()
    def on_land(self,msg): self.land=msg
    def on_battery(self,msg):
        self.battery_pct=100*float(msg.remaining) if 0<=msg.remaining<=1 else None
    def on_ack(self,msg):
        if msg.result not in (0,5):
            self.detail=f'PX4 command {msg.command}: result {msg.result}; retrying'
            self.get_logger().warning(self.detail)
    def armed(self): return self.status is not None and self.status.arming_state==2
    def set_state(self,value,detail=''):
        self.state=value; self.detail=detail; self.state_started=time.monotonic()
        if value in ('COMPLETE', 'ABORTED', 'ERROR'):
            self.ended_at=time.monotonic(); self.ended_unix=time.time()
        self.last_request=0.0
        self.get_logger().info(f'Mission state: {value} {detail}')
        self.publish_state()
    def publish_state(self):
        msg=String(); msg.data=json.dumps({'state':self.state,'mission_id':self.mission_id,
                                          'detail':self.detail,'low_battery_injected':self.injection,
                                          'route_index':self.route_index,
                                          'waypoint_count':len(self.return_route if self.state=='RETURNING' else self.route),
                                          'active_waypoints':self.return_route if self.state=='RETURNING' else self.route,
                                          'payload_status':('RELEASED' if self.payload_released else 'SKIPPED' if self.payload_skipped else 'LOWERING' if self.state=='WINCH_DROP' else 'PENDING'),
                                          'mission_started_unix':self.started_unix, 'mission_finished_unix':self.ended_unix,
                                          'mission_elapsed_s':round((self.ended_at or time.monotonic())-self.started,1) if self.mission_id else 0,
                                          'cruise_altitude_m':30.0})
        self.state_pub.publish(msg)
    def command(self,command,p1=0.0,p2=0.0):
        msg=VehicleCommand(); msg.timestamp=self.get_clock().now().nanoseconds//1000
        msg.command=command; msg.param1=float(p1); msg.param2=float(p2)
        msg.target_system=msg.source_system=1; msg.target_component=msg.source_component=1
        msg.from_external=True; self.command_pub.publish(msg)
    def poll(self):
        while rclpy.ok():
            try:
                with urllib.request.urlopen(self.base+'/mission',timeout=2) as response:
                    data=json.load(response)
                with self.lock:
                    if data.get('id') and data['id']!=self.last_command_id:
                        if time.time()-data.get('created_at',0)<10:
                            self.pending=data
                        self.last_command_id=data['id']
                    action=data.get('action')
                    if action and action['id']!=self.last_action_id:
                        self.action=action['name']; self.last_action_id=action['id']
            except Exception as exc:
                self.get_logger().debug(f'Mission API unavailable: {exc}')
            time.sleep(0.5)
    def begin(self,data):
        if self.state in self.ACTIVE or self.armed(): return
        if self.position is None or time.time()-data.get('created_at',0)>=10: return
        self.mission_id=data['id']; self.route=data['waypoints']; self.route_index=0
        self.launch_x=float(self.position.x); self.launch_y=float(self.position.y)
        self.return_route=[]; self.setpoints=0; self.started=time.monotonic()
        self.injection=False; self.low_battery_latched=False
        self.ended_at=None; self.started_unix=time.time(); self.ended_unix=None
        self.payload_released=False; self.payload_skipped=False
        self.inject_after=float(data.get('simulate_low_battery_after_seconds',-1))
        self.set_state('ARMING')
    def local(self,point):
        # Use PX4's actual EKF reference, rather than assuming it equals home.
        return wgs84_to_ned(point['latitude'],point['longitude'],
                            self.position.ref_lat,self.position.ref_lon)
    def return_home(self,reason):
        self.payload_skipped=not self.payload_released
        self.return_route=return_corridor(self.route,self.route_index)
        self.route_index=0
        self.set_state('RETURNING',reason)
    def target(self):
        if self.state in ('ARMING','TAKEOFF'):
            return self.launch_x,self.launch_y,-30.0
        path=self.return_route if self.state=='RETURNING' else self.route
        if self.state in ('DELIVERY_HOVER','WINCH_DROP'): point=self.route[-1]
        elif self.state in ('NAVIGATING','RETURNING') and self.route_index<len(path): point=path[self.route_index]
        else: return float(self.position.x),float(self.position.y),float(self.position.z)
        n,e=self.local(point); return n,e,-30.0
    def stream(self):
        stamp=self.get_clock().now().nanoseconds//1000
        mode=OffboardControlMode(); mode.timestamp=stamp; mode.position=True
        self.mode_pub.publish(mode)
        sp=TrajectorySetpoint(); sp.timestamp=stamp; sp.position=list(self.target())
        sp.yaw=0.0; self.sp_pub.publish(sp); self.setpoints+=1
    def tick(self):
        now=time.monotonic()
        fresh=self.position is not None and now-self.position_received<2 and self.status is not None and now-self.status_received<2
        if self.state not in self.ACTIVE:
            if fresh and not self.armed() and self.state=='WAITING_FOR_POSITION': self.set_state('READY')
            with self.lock: pending=self.pending
            if pending and fresh and not self.armed():
                with self.lock: self.pending=None
                self.begin(pending)
            else: return
        with self.lock: action=self.action; self.action=None
        if action=='low_battery': self.low_battery_latched=True; self.injection=True
        if action=='geofence' and self.state not in ('LANDING','RTL_FAILSAFE'):
            self.set_state('RTL_FAILSAFE','Simulated geofence breach; PX4 RTL')
        if action=='return' and self.state in ('TAKEOFF','NAVIGATING','DELIVERY_HOVER','WINCH_DROP'):
            self.return_home('Operator requested return')
        if not fresh:
            if self.state not in ('RTL_FAILSAFE','LANDING'):
                self.set_state('RTL_FAILSAFE','Position/status telemetry stale; PX4 RTL')
        if self.state=='RTL_FAILSAFE':
            self.payload_skipped=not self.payload_released
            if now-self.last_request>2:
                self.command(VehicleCommand.VEHICLE_CMD_NAV_RETURN_TO_LAUNCH); self.last_request=now
            if self.land and self.land.landed and not self.armed(): self.set_state('ABORTED','RTL landed and disarmed')
            return
        if self.state=='LANDING':
            if now-self.last_request>2:
                self.command(VehicleCommand.VEHICLE_CMD_NAV_LAND); self.last_request=now
            if self.land and self.land.landed:
                if self.armed(): self.command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM,0)
                else: self.set_state('COMPLETE','Landed and disarmed at home')
            return
        if self.state!='ARMING':
            # Detect the actual position against the same buffered demo zones as the API.
            if near_zone(self.position.ref_lat,self.position.ref_lon,self.position.x,self.position.y):
                self.set_state('RTL_FAILSAFE','Geofence buffer reached; PX4 RTL'); return
            if self.inject_after>0 and now-self.started>=self.inject_after:
                self.low_battery_latched=True; self.injection=True
            low=self.low_battery_latched or (self.battery_pct is not None and self.battery_pct<20)
            if low and self.state in ('TAKEOFF','NAVIGATING','DELIVERY_HOVER','WINCH_DROP'):
                self.return_home('Battery below 20%; delivery skipped')
        self.stream()
        elapsed=now-self.state_started
        if self.state=='ARMING':
            if elapsed>45:
                self.set_state('RTL_FAILSAFE' if self.armed() else 'ERROR','PX4 did not accept offboard/arm within 45 seconds'); return
            if self.setpoints>=20 and now-self.last_request>1:
                self.command(VehicleCommand.VEHICLE_CMD_DO_SET_MODE,1,6)
                if self.status.nav_state==14:
                    self.command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM,1)
                self.last_request=now
            if self.armed() and self.status.nav_state==14: self.set_state('TAKEOFF')
        elif not self.armed():
            self.set_state('ERROR','Vehicle disarmed before mission completion')
        elif self.status.nav_state!=14:
            self.set_state('RTL_FAILSAFE','Unexpected exit from offboard mode')
        elif self.state=='TAKEOFF' and abs(self.position.z+30)<2:
            self.route_index=1; self.set_state('NAVIGATING')
        elif self.state in ('NAVIGATING','RETURNING'):
            path=self.return_route if self.state=='RETURNING' else self.route
            n,e=self.local(path[self.route_index])
            if math.hypot(self.position.x-n,self.position.y-e)<3 and abs(self.position.z+30)<3:
                self.route_index+=1
                if self.route_index>=len(path):
                    self.set_state('LANDING' if self.state=='RETURNING' else 'DELIVERY_HOVER')
        elif self.state=='DELIVERY_HOVER' and elapsed>=3: self.set_state('WINCH_DROP','Simulating payload release')
        elif self.state=='WINCH_DROP' and elapsed>=1:
            self.payload_released=True
            self.return_home('Payload released; returning')
        if self.state in ('TAKEOFF','NAVIGATING','RETURNING') and elapsed>900:
            self.set_state('RTL_FAILSAFE','Mission segment timed out')


def main(args=None):
    rclpy.init(args=args); node=MissionExecutor()
    try: rclpy.spin(node)
    except KeyboardInterrupt: pass
    finally: node.destroy_node(); rclpy.shutdown()

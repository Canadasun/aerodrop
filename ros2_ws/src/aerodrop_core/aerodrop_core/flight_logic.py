"""Pure, testable mission path and synthetic geofence decisions."""
import math
from aerodrop_core.coordinates import EARTH_RADIUS_M

ZONES=((-900.,-700.,900.,700.),(1700.,1000.,2700.,2100.))

def return_corridor(route, next_index):
    """Retrace only reached waypoints; never jump across an unvisited bend."""
    return list(reversed(route[:max(1,min(next_index,len(route)))]))

def near_zone(ref_lat,ref_lon,north,east):
    lat=ref_lat+math.degrees(north/EARTH_RADIUS_M)
    lon=ref_lon+math.degrees(east/(EARTH_RADIUS_M*math.cos(math.radians(ref_lat))))
    x=(lon+114.0719)*111320*math.cos(math.radians(51.0447))
    y=(lat-51.0447)*111320
    return any(math.hypot(max(a-x,0,x-c),max(b-y,0,y-d))<=100 for a,b,c,d in ZONES)

from django.template.defaultfilters import first

from ..models import CellTower
from ..serializers import CellTowerSerializer

def zxvcaasfmif(towerid):
    towercode = towerid[:13]
    toweraddress = CellTower.objects.filter(_id=towercode).first()
    towerserializer = CellTowerSerializer(toweraddress)
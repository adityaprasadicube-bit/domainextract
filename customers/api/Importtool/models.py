from dataclasses import dataclass
from typing import Optional
from datetime import datetime
from bson import Decimal128

@dataclass
class CallRecord:
    __dataclass_fields__ = None
    _id: Optional[str] = None
    A_Party: Optional[str] = None
    a_mobile_code: Optional[str] = None
    B_Party: Optional[str] = None
    b_mobile_code: Optional[str] = None
    SDateTime: Optional[datetime] = None
    EDateTime: Optional[datetime] = None
    Duration: Optional[int] = None
    SDate: Optional[datetime.date] = None
    STime: Optional[str] = None
    FileCallType: Optional[str] = None
    Call_Type: Optional[str] = None
    LRN: Optional[int] = None
    First_CGI: Optional[str] = None
    Last_CGI: Optional[str] = None
    IMSI: Optional[str] = None
    IMSI_CODE : Optional[str] = None
    IMEI: Optional[str] = None
    Con_Type: Optional[str] = None
    First_Lat: Optional[Decimal128] = None  # Changed to Decimal128
    First_Long: Optional[Decimal128] = None  # Changed to Decimal128
    Last_Lat: Optional[Decimal128] = None  # Changed to Decimal128
    Last_Long: Optional[Decimal128] = None  # Changed to Decimal128
    FileServiceType: Optional[str] = None
    CallForward: Optional[str] = None
    IMEI_TAC: Optional[str] = None

    def to_dict(self):
        data = self.__dict__.copy()
        for key in ["First_Lat", "First_Long", "Last_Lat", "Last_Long"]:

            if data.get(key,''):

                data[key] = Decimal128(str(data[key])) # Convert to Decimal128
        return {k: v for k, v in data.items() if v not in [0.0,None,'','-','---']}


from dataclasses import dataclass
from typing import Optional

@dataclass
class CrimeRecord:
    __dataclass_fields__ = None
    _id: Optional[str] = None
    Crime: Optional[str] = None
    AreaLocation: Optional[str] = None
    seq_id: Optional[str] = None

    def to_dict(self):
        """Convert the dataclass object to a dictionary for MongoDB insertion, excluding `None` values."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

from dataclasses import dataclass
from typing import Optional

@dataclass
class UserRecord:
    __dataclass_fields__ = None
    _id: Optional[str] = None
    UserID: Optional[str] = None
    seq_id: Optional[str] = None

    def to_dict(self):
        """Convert the dataclass object to a dictionary for MongoDB insertion, excluding `None` values."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

@dataclass
class IPdrNexus:
    __dataclass_fields__ = None
    _id: str
    IPDR: Optional[str] = None
    FromDate: Optional[datetime] = None
    ToDate: Optional[str] = None
    Inserted: Optional[int] = None
    InsertedAt: Optional[datetime] = None
    MaxDur: Optional[int] = None
    MinDur: Optional[int] = None
    RecordType: Optional[str] = None
    Skipped: Optional[int] = None
    Duplicate: Optional[int] = None
    CrimeID: Optional[str] = None
    UserAccessID: Optional[str] = None
    Day: Optional[int] = None
    Month: Optional[int] = None
    Year: Optional[int] = None
    FileName:Optional[str] = None

    def to_dict(self):
        """Convert the dataclass object to a dictionary for MongoDB insertion, excluding `None` values."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @property
    def id(self):
        return self._id

class IPRecord:
    __dataclass_fields__ = None
    IPDR: Optional[str] = None
    MSISDN: Optional[str] = None
    MSISDN_code: Optional[str] = None
    Destination_ip: Optional[str] = None
    Destination_port: Optional[str] = None
    SDateTime: Optional[datetime] = None
    EDateTime: Optional[datetime] = None
    Duration: Optional[int] = None
    TowerID: Optional[str] = None
    IMEI: Optional[str] = None
    IMEI_TAC: Optional[str] = None
    IMSI: Optional[str] = None
    IMSI_CODE : Optional[str] = None
    DataUpload : Optional[str] = None
    DataDownload: Optional[str] = None
    Source_ip : Optional[str] = None
    Source_port:Optional[str] = None
    Translated_ip : Optional[str] = None
    Translated_port : Optional[str] = None
    ContactNo : Optional[str] = None
    NameOfPersonOrOrganization:Optional[str] = None
    AddressOfPersonOrOrganization: Optional[str] = None



    def to_dict(self):
        data = self.__dict__.copy()
        return {k: v for k, v in data.items() if v not in [None,'','-','---']}

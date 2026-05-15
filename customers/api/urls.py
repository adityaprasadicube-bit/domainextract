from django.urls import path,include

from .Importtool.ild.ild_views import upload_ild_file
from .Utilities.ip_requisition import  IPRequisitionViews
from .Utilities.wpfileconverter import RecordExtractorView
from .Whatsapp.whatsapp_views.new_summary import WhatsAppSummaryReportView
from .Whatsapp.whatsapp_views.summary_views import WhatsAppAdvancedSummaryView
from .ai_agent.views import CDRAnalyzeView
from .getsystemid import get_system_id
from .ild.ild_views.ild_views import ILDNexusView, ILDRecordView
from .signup  import signup
# from setuptools.extern import names

from .CDRSummary.cdrapisummary import CDRSummaryView
from .CDRSummary.grs import GeneralReportView
from .CommonNumbers.summarycdr import CDRcommonView
from .Delete.delete import Delete, RestoreAPI, NameAddition
from .GlobalSearch.Globalsearch import GlobalSearchApi
from .Importtool.Sdrimport.sdrcolumnconfig import  ColumnConfigAPI
from .Importtool.Sdrimport.sdrimport import SubscriberFileUploadView, AddMappingKeyView, ImportProgressView, \
    ChunkUploadView, ChunkedPreviewView, FileRowCountView
from .Importtool.Sdrimport.wacthlistcolumns import AddWatchlistMappingKeyView
from .Importtool.Sdrimport.watchlistimport import WatchlistFileUploadView, ImportProgressViewWatchlist
from .Importtool.Sdrimport.watchlistmodifications import WatchlistColumnApi, WatchlistSearchApi, \
    WatchlistmodificationsApi, WatchlistGroupManagementApi
from .Importtool.Sdrimport.watchlistnexus import WatchlistNexusApi
from .Importtool.views import upload_cdr_file, upload_ipdr_file
from .Importtool.whatsappimport.whatsapp_views import WhatsAppFilePathUploadView
from .Maps.cdrmappointing import CDRMappingReportView, CDRTowerDetailView
from .Maps.offlinemap import get_map_tile#from .Maps.offlinemap import get_map_tile
from .Search.SearchApi import SearchAPI
from .TowerDump.towerdump.towerdump_views.tower_views import TowerDumpDetailRecordDetailView, TowerDumpNexusListView
from .TowerDump.towerdump.towerdump_views.uncommon_numbers import TowerDumpAdvancedOptionsView
from .Trio.Quad import AutoCDRAnalysisView
from .Trio.trioMapping import TrioMappingApi
from .Trio.trioipdrbparty import MobileWithBpartyApi
from .Trio.trioquad import TrioNexuaApi
from .Whatsapp.whatsapp_views.whatsapp_views import WhatsAppDataView, WhatsAppNexusView
from .dashboardview import DashBoardApi, DashBoardMaxstayApi, IpdrDashboardApi, TowerDumpDashboard

from .ipdr.ipdr_views.Ipinfo_sessions import MaxOrgReportView
from .ipdr.ipdr_views.max_stay_session import  MaxStaySessionAPIView
from .ipdr.ipdr_views.circlewisenumbers import CircleWiseApiView
from .ipdr.ipdr_views.contrywiseips import CountrywiseAPIView
from .ipdr.ipdr_views.ib_summary import Summary
from .ipdr.ipdr_views.identifybparty import IdentifyBPartyView
from .ipdr.ipdr_views.imei_imsi import ImeiImsiAPIView
from .ipdr.ipdr_views.ip_views import IPDRRecordDetailView, IPDRNexusListView, IPDRExportView, IPDRCountPollView
from .ipdr.ipdr_views.macipport import MaxipportAPIView
from .ipdr.ipdr_views.requisition_view import IPRequisitionAPIView
from .ipdr.ipdrsummary import MobileNumber
from .linkanalysis import AnalysisAPI
from .loginapi import login_view
from .views import (
    NexusDetailView, NexusListView,
    CallDetailRecordDetailView, CellTowerDetailView
)
from .Newnumber.new_num_view import  NewOrMissingNumberView, CommonBPartyView,CdrToCdrView
from .TowerDump.views import TowerDumpSummaryView, ProviderotherstateAPIView

from .Watchlist.watchlistview import AddToWatchlistView



urlpatterns = [
    path('api/nexus/', NexusListView.as_view(), name='nexus-list'),
    path('api/nexus/<str:pk>/', NexusDetailView.as_view(), name='nexus-detail'),

    path('cdr-by-number/', GeneralReportView.as_view(), name='cdr-detail'),

    # path('api/cell/', CellTowerListView.as_view(), name='celltower-list'),
    # path('api/cell/<str:pk>/', CellTowerDetailView.as_view(), name='celltower-detail'),
    path('cell/search/', CellTowerDetailView.as_view(), name='celltower-search'),
    path('cdr/new-number/', NewOrMissingNumberView.as_view(), name='new-number-search'),
    path('cdr/common-number/', CommonBPartyView.as_view(), name='common-number-search'),
    path('cdr/cdr-to-cdr/', CdrToCdrView.as_view(), name='cdr-to-cdr'),
    # Nexus (summary)
    # path('api/nexus/', IPDRNexusListView.as_view(), name='nexus-list'),
    # path('api/nexus/<str:_id>/', IPDRNexusDetailView.as_view(), name='nexus-detail'),

    # IPDR records
    path('api/ipdrnexus/', IPDRNexusListView.as_view(), name='ipdrnexus-list'),
    path('ipdr-detail/', IPDRRecordDetailView.as_view(), name='ipdrrecord-detail'),
    path('ipdr-requisition/', IPRequisitionAPIView.as_view(), name='ipdrrecord-requisition'),
    path('summary-tower/', TowerDumpSummaryView.as_view(),name="summary-tower"),
    path('cdr-summary/',CDRSummaryView.as_view(),name="sdr-summary"),
    path('providerotherstate/',ProviderotherstateAPIView.as_view(),name="providerotherstate"),
    path('uploadcdr/', upload_cdr_file, name='upload_cdr'),
    path('towerdumpmapping/',TowerDumpDetailRecordDetailView.as_view(),name="towerdumpmapping"),
    path('tower-dump-advanced/', TowerDumpAdvancedOptionsView.as_view(), name="AdvancedOptions"),

    path('uploadipdr/', upload_ipdr_file, name='upload_ipdr'),
    path('ipdrdashboard/',IpdrDashboardApi.as_view(),name='dashboard'),
    path('maxipport/',MaxipportAPIView.as_view(),name='maxipport'),
    path('sessions/',MaxOrgReportView.as_view(),name="sessions"),
    path("countrywise/",CountrywiseAPIView.as_view(),name="countrywise"),
    path("imeiimsi/",ImeiImsiAPIView.as_view(),name="imsiimei"),
    path("circlewise/",CircleWiseApiView.as_view(),name="circlewise"),
    path("mobilenumber/",MobileNumber.as_view(),name="mobilenumbersummary"),
    path("ibsummary/",Summary.as_view(),name="ipdrsummarydetails"),
    path("identify-b-party/",IdentifyBPartyView.as_view(),name="identifybparty"),
    path("maxstay/",MaxStaySessionAPIView.as_view(),name="maxstay"),
    # IP Geo Database
    # path('api/ip-info/', IPDataBaseListView.as_view(), name='ipdatabase-list'),
    # path('api/ip-info/<str:_id>/', IPDataBaseDetailView.as_view(), name='ipdatabase-detail'),
    path('wpnexus/',WhatsAppNexusView.as_view(),name='whatsappnexus-list'),
    path('uploadwp/', WhatsAppFilePathUploadView.as_view(), name='whatsapp_upload_from_path'),
    path('whatsapp-mapping/', WhatsAppDataView.as_view(), name='whatsapp-records-filter'),
    path('whatsappdashboard/',WhatsAppAdvancedSummaryView.as_view(),name="whatsappDashboard"),
    path('whatsapp-summary/', WhatsAppSummaryReportView.as_view(),name="wa_summary"),

    path('cdrsummary/',CDRcommonView.as_view(),name="cdr-sumary"),
    path('towernexus/',TowerDumpNexusListView.as_view(),name='tower-summary'),
    path('delete/',Delete.as_view(),name='delete'),
    path('Restore/',RestoreAPI.as_view(),name="Restore"),
    path('tower/common-number/', CommonBPartyView.as_view(), name='common-number-search'),
    path('towerdumpdashboard/',TowerDumpDashboard.as_view(),name='towerdumpdashboard'),
    path('searchdb/',SearchAPI.as_view(),name='search'),
    path('globalsearch/',GlobalSearchApi.as_view(),name='GlobalSearch'),
    path('trioquad/',TrioNexuaApi.as_view(),name='TrioQuad'),
    path('triomapping/',TrioMappingApi.as_view(),name='TrioMapping'),
    path('tripbparty/',MobileWithBpartyApi.as_view(),name="triobparty"),
    path('quard/',AutoCDRAnalysisView.as_view(),name='quad'),
    # ...
    path('importsdr/chunk/', ChunkUploadView.as_view(), name='sdr-chunk-upload'),
    path('importsdr/', ChunkedPreviewView.as_view(), name='sdr-import-chunked'),
    path('file-row-count/<str:file_id>/', FileRowCountView.as_view(), name='file-row-count'),

    # optional: keep old single-file endpoint separately
    path('importsdr/single/', SubscriberFileUploadView.as_view(), name='sdr-import-single'),
    path('add-mapping-key/', AddMappingKeyView.as_view(), name='add-mapping-key'),
    path('column-config/',ColumnConfigAPI.as_view(),name='get_column_names'),
    path('watchlistimport/',WatchlistFileUploadView.as_view(),name='watchlistimport'),
    path('import-progresswatch/<str:import_id>/',ImportProgressViewWatchlist.as_view(),name='watchlistimportprogess'),
    path('watchlistnexus/',WatchlistNexusApi.as_view(),name="watchlistnexus"),
    path('watchlistsearch/',WatchlistSearchApi.as_view(),name='watchlistsearch'),
    path('watchlistcolumnapi/',WatchlistColumnApi.as_view(),name='watchlistcolumnapi'),
    path('watchlistcrud/',WatchlistmodificationsApi.as_view(),name='watchlistcrud'),
    path('watchlistgroup/',WatchlistGroupManagementApi.as_view(),name="WatchlistGroupManagement"),
    path('dashboardapi/',DashBoardApi.as_view(),name="DashboardAPi"),
    path('dashboardcgi/',DashBoardMaxstayApi.as_view(),name='CGIApi'),
    path('importsdr/chunk/',ChunkUploadView.as_view(),    name='sdr-chunk-upload'),
    path('linkanalysis/',AnalysisAPI.as_view(),name='Analysis'),
    path("addname/",NameAddition.as_view(),name='Name'),
    # urls.py
    path("tiles/<str:layer>/<int:z>/<int:x>/<int:y>.png", get_map_tile),
    path("cdrmappoint/",CDRMappingReportView.as_view(),name="cdrmappointing"),
    path("cdrmappoint-details/",CDRTowerDetailView.as_view(),name="cdrtowerdetails"),

    path("add-watchlist-key/",AddWatchlistMappingKeyView.as_view(),name="addcolumn"),
    path("api/ipdr-export/", IPDRExportView.as_view()),
    path("ipdr-count/", IPDRCountPollView.as_view()),
    path("login/", login_view),
    path("signup/", signup),
    path('get-system-id/', get_system_id),

    path('watchlist/',AddToWatchlistView.as_view(),    name='watchlist-add'),
    path('utilities/',RecordExtractorView.as_view(),name='whatsappiptime'),
    path("utilities_converter/",IPRequisitionViews.as_view(),name="utilitiesconverter"),


    # AI
    path("analyze/", CDRAnalyzeView.as_view(), name="cdr-analyze"),


    #ILD
    path("uploadild/", upload_ild_file, name="ild_upload"),
    path('ildnexus/',ILDNexusView.as_view(),name='ILDNexusView-list'),
    path('ild-detail/', ILDRecordView.as_view(), name='ILDRecord-detail'),


    # --- IPDR progress: supports BOTH calling styles ---
    #   GET /importprogressipdr/                        ← ?import_id=<uuid> query param
    #   GET /importprogressipdr/<uuid>/                 ← path param



]

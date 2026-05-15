from rest_framework import serializers

class IPRequisitionSerializer(serializers.Serializer):
    Destination_ip = serializers.CharField()
    Destination_port = serializers.IntegerField()
    SDateTime = serializers.DateTimeField()
    EDateTime = serializers.DateTimeField()
    Duration = serializers.FloatField()
    DataUpload = serializers.FloatField()
    DataDownload = serializers.FloatField()
    ISP = serializers.CharField(allow_null=True)
    AppHostname = serializers.CharField(allow_null=True)
    PortInfo = serializers.CharField(allow_null=True)
    PortCategory = serializers.CharField(allow_null=True)
    PortType = serializers.CharField(allow_null=True)
    Domains = serializers.CharField(allow_null=True)
    Location = serializers.CharField(allow_null=True)
    Country = serializers.CharField(allow_null=True)

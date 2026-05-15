
from datetime import date
from mongoengine import Document, fields


class WatchlistEntity(Document):
    id          = fields.StringField(primary_key=True)
    group       = fields.StringField(max_length=100, required=True)
    subgroup    = fields.StringField(max_length=100, default='')
    description = fields.StringField(default='')
    created_at  = fields.DateField(default=date.today)

    meta = {
        'db_alias':   'watchlist_db',
        'collection': 'entity',
        'indexes':    ['group', 'subgroup'],
    }

    def __str__(self):
        return f"{self.group} > {self.subgroup}"


class WatchlistEntry(Document):
    id        = fields.StringField(primary_key=True)   # uuid hex
    number    = fields.StringField(max_length=20, required=True)
    name      = fields.StringField(default='')
    imei      = fields.StringField(default='')
    cell_id   = fields.StringField(default='')
    ip        = fields.StringField(default='')
    seq_id = fields.StringField(required=True)  # → WatchlistEntity.id

    meta = {
        'db_alias':   'watchlist_db',
        'collection': 'entries',
        'indexes':    ['seq_id', 'number'],
    }

    def __str__(self):
        return f"{self.number} – {self.name}"
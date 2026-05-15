from mongoengine import get_db

from ..utils.db_connections import fetch_record


class UserDetails :
    def __init__(self):
        self.user_name = None
        self.user_id = None
        self.user_number = None
        self.user_password =None
    def user(self):
        if self.user_name is None:
            user_data =fetch_record("source_db","Logins","7702436737")
            if user_data:
                self.user_name = user_data['name']
                self.user_id = user_data['mobile']
                self.user_password = user_data['password']
                return self.user_id
            else: return "XXXXXXXXXX"
        return self.user_id

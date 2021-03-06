from __future__ import print_function
from util import responses, dynamo
from decimal import Decimal
import uuid, bcrypt, time, sys, requests, feedparser, datetime, pytz

class PodSub:
    def __init__(self):
        self.db = dynamo()
        self.responses = responses()
    
    def get_user_by_auth_token(self, auth_token):
        token_item = self.db.get_one('auth_tokens', {'token': auth_token})
        
        if token_item and token_item['expires'] > time.time():
            return self.db.get_one('users', {'id': token_item['user_id']})
        return None
        
    def register(self, body):
        exists = self.db.get_user_by_email(body['email'])
        if exists:
            raise RuntimeError(self.responses.error_message(400, 'Error: email already exists'))
        else:
            user = {
                'id': str(uuid.uuid4()), 
                'email': body['email'], 
                'passhash': bcrypt.hashpw(body['password'], bcrypt.gensalt()),
                'podcasts': [],
                'episodes': []
            }
            self.db.insert('users', user)
            
            auth_token = {
                'token': uuid.uuid4().hex, 
                'expires': Decimal(time.time()+60*60*6), 
                'user_id': user['id']
            }
            self.db.insert('auth_tokens', auth_token)
            
            return {'token': auth_token['token'], 'message': 'Registered successfully'}
    
    def login(self, body):
        user = self.db.get_user_by_email(body['email'])
        if user:
            if bcrypt.checkpw(body['password'], user['passhash']):
                auth_token = {
                    'token': uuid.uuid4().hex, 
                    'expires': Decimal(time.time()+60*60*6), 
                    'user_id': user['id']
                }
                self.db.insert('auth_tokens', auth_token)
                return {'token': auth_token['token'], 'message': 'Logged in successfully'}
            else:
                raise RuntimeError(self.responses.error_message(403, 'Authentication Error: Permission Denied'))
        else:
            raise RuntimeError(self.responses.error_message(403, 'Authentication Error: Permission Denied'))
    
    def get_podcasts(self, user, page=1, limit=25):
        if user == None:
            raise RuntimeError(self.responses.error_message(400, 'Error: Authentication failure'))
        
        if 'podcasts' in user and len(user['podcasts']) > int(limit) * (int(page) - 1):
            podcasts = []
            for i in range(int(limit) * (int(page) - 1), min(len(user['podcasts']), int(limit) * int(page))):
                if user['podcasts'][i]:
                    # TODO: use BatchGetItems
                    podcasts.append(self.db.get_one('podcasts', {'id': user['podcasts'][i]}))
            return podcasts
        else:
            return []
    
    def get_podcast(self, user, id):
        if user == None:
            raise RuntimeError(self.responses.error_message(400, 'Error: Authentication failure'))
        
        podcast = self.db.get_one('podcasts', {'id': id})
        podcast['episodes'] = self.db.get_episodes_by_podcast(id, 10, 0)
        return {'podcast': podcast}
    
    def post_podcast(self, user, body):
        if user == None:
            raise RuntimeError(self.responses.error_message(400, 'Error: Authentication failure'))
        
        if not body['url']:
            raise RuntimeError(self.responses.error_message(400, 'Error: Missing required field url'))
        else:
            if 'podcasts' not in user or not user['podcasts']:
                user['podcasts'] = []
            
            exists = self.db.get_podcast_by_url(body['url'])
            
            if exists:
                if exists['id'] not in user['podcasts']:
                    user['podcasts'].append(exists['id'])
                    self.db.insert('users', user)
                
                return {"ok": True, "podcast": exists}
            else:
                resp = requests.get(body['url'])
                if resp.status_code == 200:
                    feed = feedparser.parse(resp.text)
                    if feed['feed']:
                        podcast = {}
                        podcast['id'] = str(uuid.uuid4())
                        podcast['title'] = feed['feed']['title']
                        podcast['description'] = feed['feed']['description']
                        podcast['explicit'] = feed['feed']['itunes_explicit']
                        podcast['url'] = body['url']
                        podcast['image'] = feed['feed']['image']['href']
                        
                        self.db.insert('podcasts', podcast)
                        
                        user['podcasts'].append(podcast['id'])
                        self.db.insert('users', user)
                        
                        for ep in feed['entries']:
                            episode = self.parse_episode(podcast['id'], ep)
                            self.db.insert('episodes', episode)
                        
                        return {"ok": True, "podcast": podcast}
            
            raise RuntimeError(self.responses.error_message(resp.status_code, 'Error: could not fetch podcast URL'))
    
    def delete_podcast(self, user, id):
        if user == None:
            raise RuntimeError(self.responses.error_message(400, 'Error: Authentication failure'))
        
        if 'podcasts' in user and id in user['podcasts']:
            user['podcasts'].remove(id)
            self.db.insert('users', user)
        
        return self.responses.ok(message='Deleted podcast')
    
    def parse_episode(self, podcast_id, episode_rss):
        if podcast_id == None or len(episode_rss) == 0:
            raise RuntimeError(self.responses.error_message(400, 'Error: could not parse podcast feed'))
        else:
            episode = {}
            episode['id'] = str(uuid.uuid4())
            episode['podcast_id'] = podcast_id
            episode['guid'] = episode_rss['id']
            episode['description'] = episode_rss['summary']
            episode['release_date'] = datetime.datetime(*episode_rss['published_parsed'][:6], tzinfo=pytz.utc).isoformat()
            
            duration = episode_rss['itunes_duration'].split(':')
            if len(duration) == 3:
                episode['duration'] = int(duration[0])*3600 + int(duration[1])*60 + int(duration[2])
            else:
                episode['duration'] = 0
            
            for link in episode_rss['links']:
                if link['type'] == 'audio/mpeg':
                    episode['link'] = link['href']
                    episode['filesize'] = link['length']
            
            if 'link' not in episode:
                raise RuntimeError(self.responses.error_message(400, 'Error: could not parse podcast feed'))
            else:
                return episode

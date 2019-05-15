from twisted.python import failure
import twisted.internet.defer as defer

from chat import communication as comm
from chat.chat_server import peer


class ChatClientEndpoint(comm.Endpoint):
    def num_params_wrong(self):
        self.send('ERR_NUM_PARAMS')

    def send_me_password(self):  # ok
        self.send('RPL_PWD')

    def registered(self, nick, mail, password):  # ok
        self.send(f'OK_REG {nick} {mail} {password}')

    def taken(self, value, what='nick'):  # ok
        if what == 'nick':
            self.send(f'ERR_TAKEN nick {value}')
        elif what == 'mail':
            self.send(f'ERR_TAKEN mail {value}')
        else:
            raise ValueError('"what" parameter must be either "nick" or "mail".')

    def reg_clashed(self, value, what='nick'):  # ok
        if what == 'nick':
            self.send(f'ERR_CLASH_REG nick {value}')
        elif what == 'mail':
            self.send(f'ERR_CLASH_REG mail {value}')
        else:
            raise ValueError('"what" parameter must be either "nick" or "mail".')

    def internal_error(self, communicate):
        self.send(f'ERR_INTERNAL :{communicate}')

    def unregistered(self, user):  # ok
        self.send(f'OK_UNREG {user}')

    def logged_in(self, user):  # ok
        self.send(f'OK_LOGIN {user}')

    def login_clashed(self, user):  # ok
        self.send(f'ERR_CLASH_LOGIN {user}')

    def wrong_password(self, count):
        self.send(f'ERR_BAD_PASSWORD {str(count)}')

    def logged_out(self, nick):  # ok
        self.send(f'OK_LOGOUT {nick}')

    def list(self, channels):  # ok
        channels = ' '.join(channels)
        self.send(f'RPL_LIST {channels}')

    def is_on(self, users):  # ok
        users = ' '.join(users)
        self.send(f'RPL_ISON {users}')

    def names(self, channel, users):
        users = ' '.join(users)
        self.send(f'RPL_NAMES {channel} {users}')

    def channel_created(self, channel, creator, mode, users):
        users = ' '.join(users)
        self.send(f'OK_CREATED {channel} {creator} {mode} {users}')

    def bad_mode(self):
        self.send('ERR_BAD_MODE')

    def channel_exists(self, channel):
        self.send(f'ERR_EXISTS {channel}')

    def channel_clashed(self, channel):
        self.send(f'ERR_CLASH_CREAT {channel}')

    def channel_deleted(self, channel):
        self.send(f'OK_DELETED {channel}')

    def no_channel(self, channel):
        self.send(f'ERR_NOCHANNEL {channel}')

    def no_perms(self, perm_type, reason):
        self.send(f'ERR_NO_PERM {perm_type} :{reason}')

    def joined(self, channel):
        self.send(f'OK_JOINED {channel}')

    def user_joined(self, channel, user):
        self.send(f'JOINED {channel} {user}')

    def left(self, channel):
        self.send(f'OK_LEFT {channel}')

    def user_left(self, channel, user):
        self.send(f'LEFT {channel} {user}')

    def quit(self, channel):
        self.send(f'OK_QUIT {channel}')

    def user_quit(self, channel, user):
        self.send(f'USER_QUIT {channel} {user}')

    def added(self, channel, users):
        users = ' '.join(users)
        self.send(f'OK_ADDED {channel} {users}')

    def users_added(self, channel, users):
        users = ' '.join(users)
        self.send(f'ADDED {channel} {users}')

    def no_user(self, user):
        self.send(f'ERR_NOUSER {user}')

    def kicked(self, channel, users):
        users = ' '.join(users)
        self.send(f'OK_KICKED {channel} {users}')

    def user_kicked(self, channel, users):
        users = ' '.join(users)
        self.send(f'KICKED {channel} {users}')

    def msg(self, from_user, channel, content):
        self.send(f':{from_user} MSG {channel} :{content}')

    def notify(self, reason, notification):
        self.send(f'NOTIFY {reason} :{notification}')

    def warn(self, warning):
        self.send(f'WARN :{warning}')

    def connection_closed(self, message):
        self.send(f'CLOSED :{message}')


class InitialState(peer.State):
    def msg_REGISTER(self, message):
        self.manager.state_registering(message)

    def msg_LOGIN(self, message):
        self.manager.state_logging_in(message)

    def msg_unknown(self, message):
        self.endpoint.close_connection('Incorrect opening.')
        self.manager.lose_connection()
        super().msg_unknown(message)


class RegisteringState(peer.State):
    def __init__(self, protocol, endpoint, db, dispatcher, manager):
        super().__init__(protocol, endpoint, manager)

        self.db = db
        self.dispatcher = dispatcher
        self.reg_deferred = None

    @defer.inlineCallbacks
    def msg_REGISTER(self, message):
        nick, mail = message.params
        self.log_msg(f'received {nick} {mail}')

        try:
            nick_available, mail_available = yield self.db.account_available(nick, mail)
            if not self.connected:
                # In case connection was lost while waiting for DB response.
                return

            if nick_available and mail_available:

                @defer.inlineCallbacks
                def on_password_received(password):
                    try:
                        yield self.db.add_user(nick, mail, password)
                        self.manager.state_logged_in(nick)
                        self.dispatcher.user_registered(nick, mail, password)
                    except failure.Failure:
                        self.endpoint.internal_error('DB error, please try again.')

                def on_request_cancelled(_):
                    self.log_msg('waiting for password cancelled')

                self.reg_deferred = defer.Deferred()
                self.reg_deferred.addCallbacks(on_password_received, on_request_cancelled)
                self.endpoint.send_me_password()
            else:
                if not mail_available:
                    self.endpoint.taken(mail, 'mail')
                else:
                    self.endpoint.taken(nick, 'nick')
        except failure.Failure:
            self.endpoint.internal_error('DB error, please try again.')

    def msg_PASSWORD(self, message):
        password = message.params[0]

        if self.reg_deferred:
            d, self.reg_deferred = self.reg_deferred, None
            d.callback(password)

    def on_connection_closed(self):
        if self.reg_deferred:
            d, self.reg_deferred = self.reg_deferred, None
            d.cancel()
        super().on_connection_closed()


class LoggingInState(peer.State):
    def __init__(self, protocol, endpoint, db, dispatcher, manager):
        super().__init__(protocol, endpoint, manager)

        self.db = db
        self.dispatcher = dispatcher
        self.login_deferred = None
        self.password_countdown = 3

    @defer.inlineCallbacks
    def msg_LOGIN(self, message):
        nick = message.params[0]
        self.log_msg(f'received {nick}')

        try:
            user_registered = yield self.db.users_registered([nick])
            if not self.connected:
                return

            if user_registered and user_registered[0] == nick:

                @defer.inlineCallbacks
                def on_password_received(password):
                    try:
                        password_correct = yield self.db.password_correct(nick, password)
                        if password_correct:
                            self.manager.state_logged_in(nick)
                            self.dispatcher.user_logged_in(nick)
                        else:
                            self.password_countdown -= 1

                            if self.password_countdown > 0:
                                self.login_deferred = defer.Deferred()
                                self.login_deferred.addCallback(on_password_received)
                                self.endpoint.wrong_password(self.password_countdown)
                            else:
                                self.endpoint.connection_closed('Too many password retries.')
                                self.manager.lose_connection()
                    except failure.Failure:
                        self.endpoint.internal_error('DB error, please try again.')

                def on_request_cancelled(_):
                    self.log_msg('waiting for password cancelled')

                self.login_deferred = defer.Deferred()
                self.login_deferred.addCallbacks(on_password_received, on_request_cancelled)
                self.endpoint.send_me_password()
            else:
                self.endpoint.no_user(nick)
                self.manager.state_init()
        except failure.Failure:
            self.endpoint.internal_error('DB error, please try again.')

    def msg_PASSWORD(self, message):
        password = message.params[0]

        if self.login_deferred:
            d, self.login_deferred = self.login_deferred, None
            d.callback(password)

    def on_connection_closed(self):
        if self.login_deferred:
            d, self.login_deferred = self.login_deferred, None
            d.cancel()
        super().on_connection_closed()


class LoggedInState(peer.State):
    def __init__(self, protocol, endpoint, db, dispatcher, manager, nick):
        super().__init__(protocol, endpoint, manager)

        self.db = db
        self.dispatcher = dispatcher
        self.nick = nick

    def msg_LOGOUT(self, _):
        self.endpoint.logged_out(self.nick)
        self.dispatcher.user_logged_out(self.nick)
        self.manager.lose_connection()

    @defer.inlineCallbacks
    def msg_UNREGISTER(self, _):
        try:
            yield self.db.delete_user(self.nick)
            self.dispatcher.user_unregistered(self.nick)

            if not self.connected:
                return

            self.endpoint.unregistered(self.nick)
            self.manager.lose_connection()
        except failure.Failure:
            self.endpoint.internal_error('DB error, please try again.')

    def msg_ISON(self, message):
        users = message.params
        users_on = self.dispatcher.is_on(users)
        self.endpoint.is_on(users_on)

    @defer.inlineCallbacks
    def msg_CREATE(self, message):
        channel_name, mode = message.params[:2]
        nicks = message.params[2:]

        if mode not in ('priv', 'pub'):
            self.endpoint.bad_mode()
            return

        try:
            valid_nicks = yield self.db.users_registered(nicks)
            channel_exists = yield self.db.channel_exists(channel_name)

            if not channel_exists:
                if mode == 'pub':
                    yield self.db.add_channel(channel_name, self.nick)
                    # TODO: send invitations!
                else:
                    if self.nick not in valid_nicks:
                        valid_nicks.append(self.nick)
                    yield self.db.add_channel(channel_name, self.nick, False, valid_nicks)

                if self.connected:
                    self.endpoint.channel_created(channel_name, self.nick, mode, valid_nicks)
                # TODO: inform other servers!
                # TODO: add new Channel to Dispatcher!
            elif self.connected:
                self.endpoint.channel_exists(channel_name)
        except failure.Failure:
            self.endpoint.internal_error('DB error, please try again.')

    @defer.inlineCallbacks
    def msg_DELETE(self, message):
        channel_name = message.params[0]

        try:
            creator = yield self.db.get_channel_creator(channel_name)

            if creator:
                if creator == self.nick:
                    yield self.db.delete_channel(channel_name)

                    # TODO: inform other servers!
                    if self.connected:
                        self.endpoint.channel_deleted(channel_name)
                elif self.connected:
                    self.endpoint.no_perms('DELETE', 'You are not creator of this channel.')
            elif self.connected:
                self.endpoint.no_channel(channel_name)
        except failure.Failure:
            self.endpoint.internal_error('DB error, please try again.')

    @defer.inlineCallbacks
    def msg_LIST(self, _):
        res = yield self.db.select_all()
        print(res)
        try:
            pub_channels = yield self.db.get_pub_channels()
            priv_channels = yield self.db.get_priv_channels(self.nick)

            if self.connected:
                self.endpoint.list(['pub'] + pub_channels)
                self.endpoint.list(['priv'] + priv_channels)
        except failure.Failure:
            self.endpoint.internal_error('DB error, please try again.')


class ChatClient(peer.Peer):
    def state_init(self, message=None):
        self.state = InitialState(self.protocol, self.endpoint, self)
        if message:
            self.state.handle_message(message)

    def state_registering(self, message):
        self.state = RegisteringState(self.protocol, self.endpoint,
                                      self.db, self.dispatcher, self)
        self.state.handle_message(message)

    def state_logging_in(self, message):
        self.state = LoggingInState(self.protocol, self.endpoint,
                                    self.db, self.dispatcher, self)
        self.state.handle_message(message)

    def state_logged_in(self, nick):
        self.state = LoggedInState(self.protocol, self.endpoint,
                                   self.db, self.dispatcher, self,
                                   nick)

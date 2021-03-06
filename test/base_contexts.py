import asyncio
import asynqp
import asynqp.routing
from asyncio import test_utils
from asynqp import spec
from asynqp import protocol
from asynqp.connection import open_connection
from unittest import mock
from .util import MockServer, FakeTransport


class LoopContext:
    def given_an_event_loop(self):
        self.loop = asyncio.get_event_loop()
        asynqp.routing._TEST = True

    def tick(self):
        test_utils.run_briefly(self.loop)

    def cleanup_test_hack(self):
        asynqp.routing._TEST = False

    def async_partial(self, coro):
        """
        Schedule a coroutine which you are not expecting to complete before the end of the test.
        Disables the error log when the task is destroyed before completing.
        """
        t = asyncio.async(coro)
        t._log_destroy_pending = False
        self.tick()
        return t


class MockServerContext(LoopContext):
    def given_a_mock_server_on_the_other_end_of_the_transport(self):
        self.dispatcher = asynqp.routing.Dispatcher()
        self.protocol = protocol.AMQP(self.dispatcher, self.loop)
        self.server = MockServer(self.protocol, self.tick)
        self.transport = FakeTransport(self.server)
        self.protocol.connection_made(self.transport)


class OpenConnectionContext(MockServerContext):
    def given_an_open_connection(self):
        connection_info = {'username': 'guest', 'password': 'guest', 'virtual_host': '/'}
        task = asyncio.async(open_connection(self.loop, self.transport, self.protocol, self.dispatcher, connection_info))
        self.tick()

        start_method = spec.ConnectionStart(0, 9, {}, 'PLAIN AMQPLAIN', 'en_US')
        self.server.send_method(0, start_method)

        tune_method = spec.ConnectionTune(0, 131072, 600)
        self.frame_max = tune_method.frame_max
        self.server.send_method(0, tune_method)

        self.server.send_method(0, spec.ConnectionOpenOK(''))

        self.connection = task.result()


class OpenChannelContext(OpenConnectionContext):
    def given_an_open_channel(self):
        self.channel = self.open_channel()

    def open_channel(self, channel_id=1):
        task = asyncio.async(self.connection.open_channel(), loop=self.loop)
        self.tick()
        self.server.send_method(channel_id, spec.ChannelOpenOK(''))
        return task.result()


class QueueContext(OpenChannelContext):
    def given_a_queue(self):
        queue_name = 'my.nice.queue'
        task = asyncio.async(self.channel.declare_queue(queue_name, durable=True, exclusive=True, auto_delete=True), loop=self.loop)
        self.tick()
        self.server.send_method(self.channel.id, spec.QueueDeclareOK(queue_name, 123, 456))
        self.queue = task.result()


class ExchangeContext(OpenChannelContext):
    def given_an_exchange(self):
        self.exchange = self.make_exchange('my.nice.exchange')

    def make_exchange(self, name):
        task = asyncio.async(self.channel.declare_exchange(name, 'fanout', durable=True, auto_delete=False, internal=False),
                             loop=self.loop)
        self.tick()
        self.server.send_method(self.channel.id, spec.ExchangeDeclareOK())
        return task.result()


class BoundQueueContext(QueueContext, ExchangeContext):
    def given_a_bound_queue(self):
        task = asyncio.async(self.queue.bind(self.exchange, 'routing.key'))
        self.tick()
        self.server.send_method(self.channel.id, spec.QueueBindOK())
        self.binding = task.result()


class ConsumerContext(QueueContext):
    def given_a_consumer(self):
        self.callback = mock.Mock()
        del self.callback._is_coroutine  # :(

        task = asyncio.async(self.queue.consume(self.callback, no_local=False, no_ack=False, exclusive=False))
        self.tick()
        self.server.send_method(self.channel.id, spec.BasicConsumeOK('made.up.tag'))
        self.consumer = task.result()


class MockLoopContext(LoopContext):
    def given_an_event_loop(self):
        self.loop = mock.Mock(spec=asyncio.AbstractEventLoop)


class ProtocolContext(LoopContext):
    def given_a_connected_protocol(self):
        self.transport = mock.Mock(spec=asyncio.Transport)
        self.dispatcher = asynqp.routing.Dispatcher()
        self.protocol = protocol.AMQP(self.dispatcher, self.loop)
        self.protocol.connection_made(self.transport)


class MockDispatcherContext(LoopContext):
    def given_a_connected_protocol(self):
        self.transport = mock.Mock(spec=asyncio.Transport)
        self.dispatcher = mock.Mock(spec=asynqp.routing.Dispatcher)
        self.protocol = protocol.AMQP(self.dispatcher, self.loop)
        self.protocol.connection_made(self.transport)

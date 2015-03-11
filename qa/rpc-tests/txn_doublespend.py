#!/usr/bin/env python2
# Copyright (c) 2014 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

#
# Test proper accounting with a double-spend conflict
#

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import *
from decimal import Decimal
import os
import shutil
from time import *

class TxnMallTest(BitcoinTestFramework):

    def add_options(self, parser):
        parser.add_option("--mineblock", dest="mine_block", default=False, action="store_true",
                          help="Test double-spend of 1-confirmed transaction")

    def setup_network(self):
        # Start with split network:
        return super(TxnMallTest, self).setup_network(True)

    # Returns txid if operation was a success or None
    def wait_and_assert_operationid_status(self, myopid, in_status='success', in_errormsg=None):
        print('waiting for async operation {}'.format(myopid))
        opids = []
        opids.append(myopid)
        timeout = 300
        status = None
        errormsg = None
        txid = None
        for x in xrange(1, timeout):
            results = self.nodes[0].z_getoperationresult(opids)
            if len(results)==0:
                sleep(1)
            else:
                status = results[0]["status"]
                if status == "executing":
                    sleep(1)
                    continue
                elif status == "failed":
                    errormsg = results[0]['error']['message']
                elif status == "success":
                    txid = results[0]['result']['txid']
                break
        print('...returned status: {}'.format(status))
        assert_equal(in_status, status)
        if errormsg is not None:
            assert(in_errormsg is not None)
            assert_equal(in_errormsg in errormsg, True)
            print('...returned error: {}'.format(errormsg))
        return txid

    def run_test(self):
        mining_reward = 10
        starting_balance = mining_reward * 25

        for i in range(4):
            assert_equal(self.nodes[i].getbalance(), starting_balance)
            self.nodes[i].getnewaddress("")  # bug workaround, coins generated assigned to first getnewaddress!

        # Assign coins to foo and bar addresses:
        node0_address_foo = self.nodes[0].getnewaddress("")
        fund_foo_val = (starting_balance - (mining_reward/2))
        fund_foo_txid = self.nodes[0].sendfrom("", node0_address_foo, fund_foo_val)
        fund_foo_tx = self.nodes[0].gettransaction(fund_foo_txid)

        node0_address_bar = self.nodes[0].getnewaddress("")
        fund_bar_val = ((mining_reward/2) - 1)
        fund_bar_txid = self.nodes[0].sendfrom("", node0_address_bar, fund_bar_val)
        fund_bar_tx = self.nodes[0].gettransaction(fund_bar_txid)

        # Coins are sent to node1_address
        node1_address = self.nodes[1].getnewaddress("")

        # First: use raw transaction API to send (starting_balance - (mining_reward - 2)) BTC to node1_address,
        # but don't broadcast:
        doublespend_fee = Decimal('-.02')
        rawtx_input_0 = {}
        rawtx_input_0["txid"] = fund_foo_txid
        rawtx_input_0["vout"] = find_output(self.nodes[0], fund_foo_txid, fund_foo_val)
        rawtx_input_1 = {}
        rawtx_input_1["txid"] = fund_bar_txid
        rawtx_input_1["vout"] = find_output(self.nodes[0], fund_bar_txid, fund_bar_val)
        inputs = [rawtx_input_0, rawtx_input_1]
        change_address = self.nodes[0].getnewaddress()
        outputs = {}
        outputs[node1_address] = (starting_balance - (mining_reward - 2))
        outputs[change_address] = fund_foo_val + fund_bar_val - (starting_balance - (mining_reward - 2)) + doublespend_fee
        rawtx = self.nodes[0].createrawtransaction(inputs, outputs)
        doublespend = self.nodes[0].signrawtransaction(rawtx)
        assert_equal(doublespend["complete"], True)

        # Create two spends using 1 mining_reward BTC coin each
        opid1 = self.nodes[0].z_sendmany(node0_address_foo, [{'address': node1_address, 'amount': (mining_reward - 2)}], 0)
        txid1 = self.wait_and_assert_operationid_status(opid1)
        opid2 = self.nodes[0].z_sendmany(node0_address_bar, [{'address': node1_address, 'amount': (fund_bar_val - 1)}], 0)
        txid2 = self.wait_and_assert_operationid_status(opid2)

        # Have node0 mine a block:
        if (self.options.mine_block):
            self.nodes[0].generate(1)
            sync_blocks(self.nodes[0:2])

        tx1 = self.nodes[0].gettransaction(txid1)
        tx2 = self.nodes[0].gettransaction(txid2)

        # Node0's balance should be starting balance, plus mining_reward for another
        # matured block, minus (mining_reward - 2), minus (fund_bar_val - 1), and minus transaction fees:
        expected = starting_balance + fund_foo_tx["fee"] + fund_bar_tx["fee"]
        if self.options.mine_block: expected += mining_reward
        expected += tx1["amount"] + tx1["fee"]
        expected += tx2["amount"] + tx2["fee"]
        assert_equal(self.nodes[0].getbalance(), expected)
        z_totalbalance = self.nodes[0].z_gettotalbalance(0)

        # foo and bar addresses should be empty, because z_sendmany
        # sends transparent change to a new t-address:
        assert_equal(self.nodes[0].z_getbalance(node0_address_foo, 0), 0)
        assert_equal(self.nodes[0].z_getbalance(node0_address_bar, 0), 0)

        if self.options.mine_block:
            assert_equal(tx1["confirmations"], 1)
            assert_equal(tx2["confirmations"], 1)
            # Node1's total balance should be its starting balance plus both transaction amounts:
            assert_equal(self.nodes[1].getbalance(""), starting_balance - (tx1["amount"]+tx2["amount"]))
        else:
            assert_equal(tx1["confirmations"], 0)
            assert_equal(tx2["confirmations"], 0)

        # Now give doublespend and its parents to miner:
        self.nodes[2].sendrawtransaction(fund_foo_tx["hex"])
        self.nodes[2].sendrawtransaction(fund_bar_tx["hex"])
        self.nodes[2].sendrawtransaction(doublespend["hex"])
        # ... mine a block...
        self.nodes[2].generate(1)

        # Reconnect the split network, and sync chain:
        connect_nodes(self.nodes[1], 2)
        self.nodes[2].generate(1)  # Mine another block to make sure we sync
        sync_blocks(self.nodes)

        # Re-fetch transaction info:
        tx1 = self.nodes[0].gettransaction(txid1)
        tx2 = self.nodes[0].gettransaction(txid2)

        # Both transactions should be conflicted
        assert_equal(tx1["confirmations"], -1)
        assert_equal(tx2["confirmations"], -1)

        # Node0's total balance should be starting balance, plus (mining_reward * 2) for
        # two more matured blocks, minus (starting_balance - (mining_reward - 2)) for the double-spend,
        # plus fees (which are negative):
        expected = starting_balance + (mining_reward * 2) - (starting_balance - (mining_reward - 2)) + fund_foo_tx["fee"] + fund_bar_tx["fee"] + doublespend_fee
        assert_equal(self.nodes[0].getbalance(), expected)
        assert_equal(self.nodes[0].getbalance("*"), expected)
        assert_equal(self.nodes[0].getbalance(""), expected)

        # Node1's total balance should be its starting balance plus the doublespend:
        assert_equal(self.nodes[1].getbalance(""), starting_balance + (starting_balance - (mining_reward - 2)))

if __name__ == '__main__':
    TxnMallTest().main()


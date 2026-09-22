# Blockchain

> **Source**: [Blockchain](https://compass.rauias.com/science-technology/blockchain-technology/)

---

## Blockchain Technology: Fundamentally a distributed ledger

When I think of blockchain the first thing that comes to my mind is that it is fundamentally a ledger. Not only that, it is a distributed ledger. One universal ledger that is shared with everyone.

Ledgers are everywhere. Your land records, the balance in your bank account, the votes you cast in an election, the [stock market](https://compass.rauias.com/current-affairs/stock-market-regulation-in-india/) transaction, all of these are simply ledgers.

A ledger is simply a book that records ownership details of an asset. And whenever there is a transaction, it records the transfer of ownership.

![image 73](https://i0.wp.com/compass.rauias.com/wp-content/uploads/2023/08/image-73.png?resize=304%2C338&ssl=1)

Given that it is a record that captures ownership of an asset, it is a very sensitive book. Nobody should be able to change the details arbitrarily. This is especially true when two unknown people are transacting. A third party is needed to ensure trust between two unknown people. People who do this are called intermediaries.

Banks, revenue department, [election commission](https://compass.rauias.com/polity/election-commission/) are all examples of intermediaries.

Imagine a world in which there is no intermediary. This is what a blockchain-run world looks like.

A lot of assets in the world are simply an entry in the ledger. This is particularly true in the world that is deeply financialized. Most of the money does not exist in physical form but as an entry in ledgers.

When you pay ₹100 to a shopkeeper for exchange of goods on say a UPI app there is no physical money that travels. It only changes the ledger that your bank maintains on your behalf on one hand and the ledger that the shopkeepers bank maintains on his/her behalf on the other.

This looks simple but it’s a tedious process. This becomes more tedious if you were to transfer money to a friend in a different country. Then there are multiple ledgers that change along the way. Your bank in India, your friends bank in another country, the SWIFT network etc. More the number of intermediaries, more the number of ledgers, more tedious the process and higher the cost of transaction.

Imagine if instead of multiple ledgers there was one universal shared ledger on the cloud. This is called a distributed ledger.

### **Understanding Distributed Ledgers with Analogies**

#### **Blockchain: Old wine in a new bottleThe Yapese islanders and their Rai stone**

In around 500 AD, people of a small [pacific island](https://compass.rauias.com/current-affairs/fipic-forum-for-india-pacific-islands-cooperation/) in Micronesia had designed for themselves a unique ledger system that would help them trade goods and services.

They used large immovable stones called Rai stone made of limestone to trade for goods and services. Every time a good was exchanged or a service rendered, all the people would assemble around a Rai stone to witness the transaction. Say A wanted to sell 1 Kg rice to B in exchange of a Rai stone. Everyone in the island would assemble around the Rai stone to witness transaction. In effect what really happened was the creation of a shared memory of the transaction. This is like a ledger entry but in the minds of all the islanders. Nobody could tamper with this shared ledger unless at least 51% of the Yapese arrived at a consensus to cheat.

This is an example of a distributed ledger and this is exactly what a Blockchain is.

![image 74](https://i0.wp.com/compass.rauias.com/wp-content/uploads/2023/08/image-74.png?resize=219%2C153&ssl=1)

![image 75](https://i0.wp.com/compass.rauias.com/wp-content/uploads/2023/08/image-75.png?resize=216%2C154&ssl=1)

Imagine every transaction that happens in the world today happens this way. In other words, what if every transaction occurs in the presence of everybody in the world.

No one can cheat unless 51% decide to cheat. But how do we assemble all the people in the world to witness the transaction. Simply use the internet. With internet you can implement this concept globally. For instance, make a google doc and share it with everyone. If 51% of the computers in the network agrees, record the transaction in the google doc. This is what a blockchain is, a distributed ledger.

However, one problem with this kind of shared ledger is that everyone can see. Also wait a distributed ledger is universal meaning it is accessible to everyone. Anyone can tamper it right?! Anybody can fudge it. How do you trust unknown people accessing your financial details. In short, a distributed ledger which is openly accessible to everyone has no integrity. **Blockchain technology has a way to bring integrity to the distributed ledger.(you can remember this as the second most important thing ).

How does it happen? So we have to find a way to somehow hide the content of the transaction but still use the advantage of the shared ledger. This is where cryptography comes into the picture. We use cryptography to create a sort of ‘fingerprint’ of the transaction and whenever someone tries to tamper the transaction, the fingerprint changes alerting everyone in the network.

Essentially in this shared digital ledger you are not really seeing the content of the transaction but a ‘fingerprint’ of the transaction. This is how privacy and security are ensured in a blockchain.

Another bit we need to understand is the word consensus here. It is essential to record a transaction in the ledger. In Yapese Island the transaction was recorded in the shared memory of the community when A and B executed the transaction in everybody’s presence.

In blockchain a transaction is initiated by one party and is consummated only after the second party agrees, authenticates, and ratifies it. In other words, only after there is consensus the transaction is added to the ledger.

In a blockchain this is achieved through various means. These means are typically proof-of-work and proof-of-stake.

#### **Securing the Distributed Ledger with Cryptography**

While a distributed ledger offers transparency, concerns about privacy and security arise. Blockchain addresses these concerns by using cryptography.

Rather than displaying the content of transactions openly, a blockchain shows a cryptographic "fingerprint" or hash value.

This fingerprint changes if anyone attempts to tamper with the transaction, alerting the entire network. Cryptography ensures privacy and security within the blockchain ecosystem.

#### **Consensus Mechanisms in Blockchain**

To record a transaction in the ledger, consensus among the network participants is crucial. In blockchain, a transaction is initiated by one party and finalized only after the second party agrees, authenticates, and ratifies it.

Various consensus mechanisms, such as proof-of-work and proof-of-stake, are employed in blockchain to achieve this agreement. Proof-of-work involves solving complex puzzles that require significant computational power, ensuring that only serious participants can add transactions to the blockchain.

Proof-of-stake, on the other hand, means that the transaction needs to be verified and agreed upon by the majority of stakeholders. These consensus mechanisms ensure collective decision-making, preventing any single person or group from controlling the blockchain.

#### **Key Functions of Blockchain**

Blockchain technology serves several purposes:

- Only the rightful owner should be able to create a transaction. This is achieved using digital signatures, which use a combination of public and private keys to prove ownership.

- Transactions should be unchangeable. To achieve this, blockchain uses hash values, which are unique codes representing each transaction. Any alteration to the transaction will result in a change in the hash value, alerting the network.

- The history of transactions provides ownership details. Hash referencing links each transaction to the previous one, creating a chain of transactions and hence the name blockchain. This ensures the integrity and immutability of the ledger.

- Appending a transaction to the ledger requires consensus from the majority of participants. Consensus mechanisms like proof-of-work and proof-of-stake ensure that transactions are agreed upon by the network before being added to the ledger.

#### **Technologies in Blockchain**

Blockchain integrates various technologies to implement a distributed ledger. It utilizes digital signatures for creating transactions, hash values for security, and hash referencing for recording ownership details. Consensus mechanisms like proof-of-work and proof-of-stake ensure the integrity of the ledger.

#### **The Goal and Characteristics of Blockchain**

The primary goal of blockchain is to manage ownership rights of digital goods. Blockchain exhibits several characteristics, including being immutable, append-only, ordered, time-stamped, open and transparent, secure, eventually consistent, providing provenance, and operating in a trust-less environment.

### **Scope and Applications of BlockchainBlockchain has a wide scope of applications:**

- **Proof of existence:Blockchain can be used for registries like land, patents, and licenses.

- **Proof of non-existence:Blockchain can facilitate registries for complaints, crime data, and sexual offenders.

- **Proof of time and order:Blockchain can provide a trusted record of the sequence and timing of events.

- **Proof of identity:Blockchain can be used for identification purposes, such as beneficiary identification, Aadhar, voter ID, driver's licenses, and passports.

- **Proof of authorship:Blockchain's security features make it suitable for online publishing and content writing.

- **Proof of ownership:Blockchain can manage ownership rights of various assets like property, cars, [digital currencies](https://compass.rauias.com/current-affairs/central-bank-digital-currency-cbdc/), insurance, and shares.

#### **Applications of Blockchain**

Blockchain can be applied to manage ownership rights of [various digital assets](https://compass.rauias.com/current-affairs/virtual-digital-assets/), including digital currency, micropayments, digital goods, identity proof, notary services, tax, voting, and record management. These applications have the potential to revolutionize industries by increasing efficiency, reducing fraud, and promoting transparency.

#### **Non-Fungible Tokens (NFTs)**

NFTs are used to represent uniqueness in the virtual world.

They address the problem of copying digital assets like art, currency, or digital twins.

By attaching a unique code to each asset, NFTs prevent duplication and enable the representation of one-of-a-kind digital items using a set of 0s and 1s.

#### **What all can an NFT do?**

- Identity management in the virtual world

- Deed for virtual assets

- Enable virtualize the real world

- Virtual versions of physical items

- Value-carries over Web3.0

- Can act as digital passport to entry into a virtual world like virtual museums.

#### **Characteristics**

- Non-transferable and unique

- Unique digital collectibles backed by a blockchain network

- Gives proof-of-ownership.

- But does not stop you from exchange or duplication of actual digital data stored.

- Can be traded like any asset but not exchanged like currency as it is non-fungible i.e it cannot change forms.

![image 76](https://i0.wp.com/compass.rauias.com/wp-content/uploads/2023/08/image-76.png?resize=344%2C203&ssl=1)

**Conclusion**

Blockchain technology and NFTs are poised to revolutionize various industries and open up new possibilities in the digital world. By leveraging distributed ledgers, cryptography, consensus mechanisms, and the representation of uniqueness through NFTs, these technologies provide secure, transparent, and decentralized solutions for managing ownership rights and transactions. As their adoption continues to grow, we can expect to see significant transformations across multiple sectors.

### **PROOF OF STAKE VS PROOF OF WORK**

- Leading cryptocurrency migrated to ‘Proof of Stake’ consensus mechanism from the earlier ‘Proof of work’ mechanism.

- Because blockchains do not have any central authority keeping track of transactions and balances, they need a way for users to agree on who owns what. This is known as consensus mechanism.

#### **Proof of Stake MECHANISM**

Proof of Stake are consensus mechanism used by blockchains to achieve distributed consensus. In this mechanism, mining is replaced by staking. The owners of blockchains offer their coins as collateral (staking) for the chance to validate blocks and then become validators. Validators are selected randomly. Blocks are validated by more than one validator, and when a specific number of the validators verify that the block is accurate, it is finalized and closed.

#### **Benefits of Proof of Stake mechanism**

- Better energy efficiency as there is no need to use crypto-mining computations required in Proof of Work.

- Lower barriers to entry, reduced hardware requirements.

- Reduced centralisation risk: Proof of stake should lead to more nodes securing the network.

#### **Proof of Work mechanism**

Proof of Work was first widely used blockchain consensus mechanism pioneered in Bitcoin. It requires users to mine or complete complex computational puzzles before submitting new transactions to the network. This expenditure of time and computation power is costly and has high environmental cost in the form of energy required to conduct mining.

**Proof of StakeProof of Work**

|  |  |
| --- | --- |
| Block creators are called validators. | Block creators are called miners. |
| Participants must own coins or tokens to become a validator. | Participants must buy equipment and energy to become a miner. |
| Energy efficient | Not energy efficient |
| Security through community control. | Robust security due to expensive upfront requirement. |
| Validators receive transactions fees as rewards. | Miners receive block rewards. |

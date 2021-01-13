/* -*- Mode:C++; c-file-style:"gnu"; indent-tabs-mode:nil; -*- */
#ifndef DISTRIBUTED_ML_TEST_H
#define DISTRIBUTED_ML_TEST_H

#include "ns3/packet.h"
#include "ns3/ptr.h"
#include "ns3/seq-ts-size-header.h"
#include "ns3/socket.h"
#include "ns3/data-rate.h"

#include <iostream>
#include <vector>
#include <unordered_map>
#include "ns3/simulator.h"
#include "ns3/mpi-interface.h"
#include "ns3/trace-source-accessor.h"
#include "ns3/log.h"



namespace ns3 {
//TODO:: maybe in the future, we will change the data structure of MTensor
template <class T>
class MTensor
{
public:
    MTensor<T>(): m_size(0){};
    
    /* If initialized MTensor without stating the value of m_data, we create a new m_data with the size,
        But setting the m_size to be 0.
    */
    MTensor<T>(uint32_t size): m_size(0) { m_data = new T[size]; }; 
    
    MTensor<T>(uint64_t t_addr, uint32_t size): m_size(size) {m_data = (T*)(t_addr);};

    MTensor<T>(T* data, uint32_t size): m_data(data), m_size(size){};

    uint32_t size(){
        return m_size;
    }

    T* data(){
        return m_data;
    }

    bool empty(){
        return bool(m_size==0);
    }

    void zero(){
        for (auto i=0; i<int(m_size); i++){
            *(m_data+i) = 0;
        }
    }

    void clear(){
        if(m_data){
            delete m_data;
            m_data=NULL;
            m_size=0;
        }
        
    }

    //Copy the value of another MTensor, while keeps the addr unchanged
    void copy(MTensor<T> m){
        if(!m_data){
            m_data = m.data();
            m_size = m.size();
        }else{
            NS_ABORT_IF (m.size() != m_size);
            memcpy(m_data, m.data(), m_size*sizeof(T));

        }
    }

    //Copy the partial value of another MTensor, start from the start point.
    void copy_from(MTensor<T> m, uint32_t start){
        NS_ABORT_IF (!m_data);
        NS_ABORT_IF (start+m_size>m.size());
        memcpy(m_data, m.data()+start, m_size*sizeof(T));
    }

    //Append the value of another MTensor, while keeps the addr unchanged
    //BE CAREFUL to use this function, it may causes memeory leakage
    void append(MTensor<T> m){
        memcpy(m_data+m_size, m.data(), m.size()*sizeof(T));
        m_size += m.size();
    }


    inline T operator[](const uint32_t i){
        return *(m_data+i);
    };


    inline MTensor<T> operator+(const T val){

        T* n_data = new T[m_size];        
        for(auto i=0; i<int(m_size); i++){
            *(n_data+i) = *(m_data+i) + val;
        }
        return MTensor<T>(n_data, m_size);
    }

    inline MTensor<T> operator+(MTensor<T> m){
        NS_ABORT_IF (m.size() != m_size);
        T* n_data = new T[m_size];

        T* data = m.data();
        
        for(auto i=0; i<int(m_size); i++){
            *(n_data+i) = *(m_data+i) + *(data+i);
        }
        return MTensor<T>(n_data, m_size);
    }

    inline MTensor<T> operator-(MTensor<T> m){
        NS_ABORT_IF (m.size() != m_size);
        T* n_data = new T[m_size];

        T* data = m.data();
        
        for(auto i=0; i<int(m_size); i++){
            *(n_data+i) = *(m_data+i) - *(data+i);
        }
        return MTensor<T>(n_data, m_size);
    }

    inline MTensor<T> operator*(const T val){
        T* n_data = new T[m_size];

        for(auto i=0; i<int(m_size); i++){
            *(n_data+i) = *(m_data+i) * val;
        }
        return MTensor(n_data, m_size);
    }

    inline MTensor<T> operator/(const T val){
        T* n_data = new T[m_size];

        for(auto i=0; i<int(m_size); i++){
            *(n_data+i) = *(m_data+i) / val;
        }
        return MTensor<T>(n_data, m_size);
    }

private:
    T* m_data = NULL;
    uint32_t m_size;
};



Ptr<Packet> ToPackets(MTensor<float>& tensor);

MTensor<float> PacketsTo(const Ptr<Packet>& packet);


class MlBuffer{
public:
    MlBuffer(){};

    MlBuffer (uint64_t t_addr, uint32_t size) {m_tensor = MTensor<float> (t_addr, size);};

    MlBuffer (MTensor<float> tensor): m_tensor(tensor){};

    MlBuffer (Ptr<Packet>& packet){m_tensor = PacketsTo(packet);};

    MlBuffer (uint64_t* t_addrs, std::vector<uint32_t> sizes):m_addrs(t_addrs), m_sizes(sizes) {CopyFromMem();};

    void SetTensor(MTensor<float> tensor){m_tensor=tensor;};

    void CopyFromMem(void);

    void PasteToMem(void);

    virtual ~MlBuffer(){};

    uint32_t GetMaxSeq(void);

    MTensor<float> GetBuffer(void);  // The GetBuffer function changes the seqBuffer into a MTensor.

    bool freshMTensor(void);

    float operator[](const uint32_t i);

    Ptr<Packet> GetPacket(void);  // convert m_tensor to a ns3::packet

    uint32_t size(void);           // return m_tensor.size()

    MTensor<float> GetTensor(void); //return m_tensor;

    void FedSend(Ptr<Socket> socket, uint32_t m_packetSize, DataRate m_dataRate);

    uint32_t FedAvg(Ptr<Packet>& packet);

    uint32_t FedUpdate(Ptr<Packet>& packet);

    void Zero(void);   // Set the data of m_tensor and seqBuffer to be 0, set seqCount to be 0

private:

    void AvgBuff(uint32_t seq, const Ptr<Packet>& packet);

    void UpdateBuff(uint32_t seq, const Ptr<Packet>& packet);

    Ptr<Packet> preSend(Ptr<Packet>& packet, SeqTsSizeHeader& header, uint32_t m_packetSize);
     
	void afterSend(Ptr<Packet>& packet, SeqTsSizeHeader& header);

    void BulkSend(const Ptr<Socket>& socket, Ptr<Packet>& packet, SeqTsSizeHeader& header, uint32_t m_packetSize, DataRate m_dataRate, int actual);


	bool fresh_flag = false;  // a flag to indicate whether m_tensor should be freshed

    MTensor<float> m_tensor;

    uint32_t maxSeq = 0;  //use to record the max seq from header
    
    std::unordered_map<uint32_t, MTensor<float>> seqBuffer;

    std::unordered_map<uint32_t, uint32_t> seqCount;

    uint64_t* m_addrs=NULL;
    std::vector<uint32_t> m_sizes;

};



}

#endif /* DISTRIBUTED_ML_H */

